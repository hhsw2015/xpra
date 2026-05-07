---
name: update-rpm-spec
description: Bump one or more xpra RPM specs in packaging/rpm/ to the latest upstream release. Edits Version/Release/sha256, prepends a changelog entry, validates with rpmbuild, and commits per-spec. Use when the user asks to "update", "bump", or "upgrade" a spec to its latest version, or names specific spec files in packaging/rpm/.
---

# update-rpm-spec

Bumps xpra's RPM specs in `packaging/rpm/` to upstream's latest release.

## Inputs

The user names one or more specs (e.g. `python3-wheel.spec`, `nasm.spec`) and optionally a target version. If no version is given, query upstream for the latest stable.

## Steps

For each spec, do these in order. Don't batch across specs — one full cycle (edit → validate → commit) per spec, so a build failure on spec N doesn't block specs 1..N-1.

### 1. Read the current spec

Note four things from the spec file:
- `Name:` and `Version:` (the current ones)
- `Release:` (will reset to `1%{?dist}` if currently higher)
- The `Source0:` URL pattern (PyPI sdist, GitHub release, freedesktop GitLab tag, etc.)
- The existing sha256 in the `%prep` checksum guard (`if [ "${sha256}" != "..." ]`)

### 2. Find the latest upstream version

Map by source pattern:

| Source pattern | Lookup |
|---|---|
| `files.pythonhosted.org/.../<pkg>/<pkg>-%{version}.tar.gz` | `curl -s https://pypi.org/pypi/<pkg>/json \| python3 -c "import sys,json; print(json.load(sys.stdin)['info']['version'])"` |
| `github.com/<org>/<repo>/archive/v%{version}/...` | `curl -s https://api.github.com/repos/<org>/<repo>/releases/latest \| python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])"` |
| `gitlab.freedesktop.org/.../tags/...` | `curl -s "https://gitlab.freedesktop.org/api/v4/projects/<urlencoded-path>/repository/tags?per_page=5"` |
| `nasm.us/pub/nasm/releasebuilds/<ver>/...` | GitHub `netwide-assembler/nasm` tags, filter to `nasm-N.N` (skip `rcN`) |

Be careful with PyPI package name vs spec `pypi_name` — they sometimes differ from the import name. Examples: `nvidia-ml-py` (sdist) → `pynvml` (import), `dbus-python` → `dbus`.

### 3. Fetch the new tarball and compute sha256

```sh
curl -s https://pypi.org/pypi/<pkg>/<version>/json \
  | python3 -c "import sys,json; d=json.load(sys.stdin); [print(u['url'], u['digests']['sha256']) for u in d['urls'] if u['packagetype']=='sdist']"
```

For non-PyPI: download the tarball with `curl -sLO ...` to `~/rpmbuild/SOURCES/` and `sha256sum` it. Always verify the sha256 of the on-disk file matches what the spec will check — this is what `rpmbuild` validates.

### 4. Edit the spec

- `Version:` → new version
- `Release:` → `1%{?dist}` if currently higher (e.g. Fedora rebuild bumped it)
- sha256 in `%prep` → new checksum
- `%changelog` → prepend a new entry. Format:
  ```
  * <Day Mon DD YYYY> Antoine Martin <antoine@xpra.org> - <version>-<release>
  - new upstream release
  ```
  Use `date +"%a %b %d %Y"` for today (the leading `*` and `Antoine Martin <antoine@xpra.org>` line stays consistent across xpra-authored entries).

Don't touch other lines unless step 5 forces it.

### 5. Validate with rpmbuild

```sh
PYTHON3=python3.14 rpmbuild -ba packaging/rpm/<spec>      # for python3-* specs
rpmbuild -ba packaging/rpm/<spec>                          # for native libs (nasm, openh264, etc.)
```

Run from the repo root. If the build succeeds, the resulting `.rpm` lands in `~/rpmbuild/RPMS/<arch>/`. Keep going.

If it fails, debug — the failure usually means the upstream changed something the spec wasn't ready for. See "Common breakage patterns" below. Fix the spec, re-run rpmbuild, repeat until it passes.

### 6. Commit

One commit per spec. Message format the user established:

```
git add packaging/rpm/<spec>
git commit -m "<libname> <newversion>" packaging/rpm/<spec>
```

`<libname>` is the project's "natural" name, not the rpm package name. Examples seen so far:

| Spec file | Commit subject |
|---|---|
| `python3-wheel.spec` | `wheel 0.47.0` |
| `python3-pytools.spec` | `pytools 2026.1` |
| `python3-pynvml.spec` | `pynvml 13.595.45` |
| `python3-pylsqpack.spec` | `pylsqpack 0.3.24` |
| `python3-dbus.spec` | `dbus-python 1.4.0` |
| `nasm.spec` | `nasm 3.01` |
| `python3-pybind11.spec` | `pybind11 3.0.4` |

Then proceed to the next spec.

## Common breakage patterns

These are the failures we've actually hit; check for them when rpmbuild fails.

### Build backend changed

A python package switches from setuptools to hatchling / meson-python / scikit-build-core. Symptoms: `Cannot import 'hatchling.build'`, `setup.py: No such file or directory`, etc.

- **hatchling / setuptools-based with `pip wheel`** (e.g. pytools 2024 → 2026): typically just works if `pip wheel . --no-deps` is the build line and build isolation can reach PyPI to fetch the backend. If a leftover `# %pyproject_wheel` comment is in `%build`, **remove it** — RPM expands `%macros` before treating `#` as a comment, so a multi-line macro leaks into the script and shadows the intended pip line.
- **scikit-build-core (CMake-based, no setup.py)** (e.g. pybind11 2.x → 3.x): drop the `setup.py build` and `%py3_install` lines. Keep the existing `%cmake`/`%make_install` for headers/cmake/pkgconfig. Install the python module with `cp -r <pkgname> %{buildroot}%{python3_sitearch}/`. If a console-script entry point (e.g. `pybind11-config`) is no longer produced by CMake but is declared in `pyproject.toml`'s `[project.scripts]`, hand-roll a wrapper:
  ```
  cat > %{buildroot}%{_bindir}/<scriptname> <<EOF
  #!/usr/bin/%{python3}
  from <pkg>.__main__ import main
  main()
  EOF
  chmod +x %{buildroot}%{_bindir}/<scriptname>
  ```
  Drop the matching `egg-info` line from `%files` if the new install path produces `dist-info` instead.
- **meson-python (no shipped configure)** (e.g. dbus-python 1.3.2 → 1.4.0): autotools-based specs that did `%configure && make` may break because the upstream sdist no longer ships a pre-generated `configure` script. Fix: add `NOCONFIGURE=1 ./autogen.sh` before `%configure`. (`NOCONFIGURE=1` stops autogen from running configure itself, so `%configure` still applies the right flags.)

### Comment-leak through `%macros`

If a `%build` or `%install` block has `# %something` and `something` is a defined macro, RPM expands the macro and only the first physical line of the result is commented. Solution: just delete the comment line. Don't hide macros inside spec comments.

### Stale cwd before rpmbuild

If a previous step `cd`'d into a temp dir and that dir was removed, rpmbuild will hit `getcwd: cannot access parent directories`. Always `cd /home/antoine/projects/xpra` (or use absolute paths) before the rpmbuild call.

### "File not found" in `%files`

`%files` references a path the install no longer produced. Either:
- the install macro changed (e.g. the bindir script disappeared) — synthesize it manually as above, or
- the upstream switched layouts (e.g. `egg-info` → `dist-info`) — update the path in `%files`.

## What not to do

- Don't bump `Release` for unrelated reasons. Keep it at the current value, except reset to 1 on a version bump.
- Don't add `BuildRequires:` for things that already work via build isolation. The xpra spec style favors minimal explicit deps.
- Don't run `git rebase -i` to "tidy" the commit history — this skill produces one commit per spec, in order. If the user wants to reorder/drop, they'll say so.
- Don't push. The user pushes themselves.
- Don't use `--amend`. Each version bump is a fresh commit.

## When to stop and ask

- The new version is a major bump (e.g. 1.x → 2.x for numpy, 2.x → 3.x for pybind11) and known to break ABI/API for downstream consumers (other packages in `packaging/rpm/` that link against it). Flag it before committing — the user may want to coordinate.
- rpmbuild fails for a reason you can't trace back to one of the patterns above.
- Upstream renamed the project, moved the source URL, or changed the license — those need a human decision.
