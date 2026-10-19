# tsbuddy for AOS releases (issue #47)

Source read: `github.com/bgbyte/tsbuddy` at commit `7f5245f` (2026-09-17, `pyproject.toml` version 0.0.47, not yet on PyPI),
and the PyPI wheel `tsbuddy-0.0.46-py3-none-any.whl` (2026-09-09). `aos/aosdl.py` is identical in both (ignoring line
endings); only `ga_index.json` / `ga_latest.json` differ (0.0.47 adds 8.11.162.R01). Line numbers below are for `7f5245f`.
Nothing was executed against any host; the code was only read (and syntax-compiled under Python 3.10.11).

## Conclusion

**Do not import or subprocess tsbuddy's `aos` tools; reimplement the small parts BLab needs and keep tsbuddy's two JSON files
as the release data.** The whole AOS feature is one 477-line, interactive, print-only module (`src/tsbuddy/aos/aosdl.py`).
Its useful content is (1) a model → family → image-file table, (2) two hand-maintained JSON files giving the GA build per
release per family and the latest GA per family, and (3) one URL pattern on an internal HTTP server
(`http://10.46.4.37/bop/images/OS_<maj>_<build>_<rel>/<family>/Release/<Img>`). The upgrade itself is `su` + `curl` on the
switch, then `reload from <dir> no rollback-timeout`, with no error checking, no checksum, no wait-for-reboot and no
verification. Every entry point (`aosga`, `aosup`, `aosdl`) calls `input()`/`getpass()`. The only non-interactive call is
`main(folder_name, reload_when_finished, found_ga_build, hosts)`, which still prints instead of returning, swallows all
errors, and would need `su` (a Linux shell) on the switch. Installing tsbuddy also pulls pandas, scapy and xlsxwriter
into BLab's image for no benefit. BLab already has its own SSH/CLI layer to the switches. The better fit is a small
BLab module that: maps `Switch.model` to a family and image (copying tsbuddy's table), reads the GA data (vendored or
synced from tsbuddy's JSON, with credit), builds the image URL, and drives the copy, reload, wait and verify steps itself
with real progress and error handling. Whether the switch pulls the image or BLab pushes it depends on reachability
(see the open questions).

**What Titouan needs to decide or provide**

1. **Reachability of `10.46.4.37`.** Can the lab switches (and/or the BLab server 10.69.144.180) reach
   `http://10.46.4.37/bop/images/`? tsbuddy assumes the *switch* pulls from it directly. If only the server can reach it,
   BLab must download the image and push it to the switch (SFTP/SCP to `/flash/<dir>/`), or relay it.
2. **Is `10.46.4.37` a stable, sanctioned source?** It is hard-coded with no config, no credentials and plain HTTP. Is there
   an official name or mirror, and does the ALE-internal private tsbuddy (`github.com/tsbuddy/tsbuddy`, employee-only) use
   something different? That repo was not readable here.
3. **Source of truth for "latest GA" and "compatible releases".** tsbuddy's JSON is updated by hand by its maintainer
   (last updated 2026-09-17). Is it fine for BLab to follow it (vendor a copy, or fetch it from GitHub raw), or should the
   list come from the image server's directory listing?
4. **License.** The repo has no LICENSE file, and the MIT classifier is commented out (`pyproject.toml:17`). Copying the
   tables or JSON needs the maintainer's OK (a colleague, so this should be easy to get). Better still, ask him to add a
   license.
5. **Secure Boot images.** CHANGELOG 0.0.39 says 8.10R04 support is "default image only, no choice between Secure Boot or
   Non Secure Boot". Do any lab switches need the other image?

## 1. Where images come from, credentials, reachability

- **GA lookup is offline.** `aosga` (`lookup_ga_build`, `aosdl.py:261-323`) reads two JSON files bundled in the package and
  loaded at import time (`aosdl.py:133-139`): `aos/ga_index.json` and `aos/ga_latest.json`. There is no network call.
- **Download host:** hard-coded in `main()`. `base_ip = "http://10.46.4.37"` and `base_dir = "/bop/images"`
  (`aosdl.py:468-469`). This has not changed since at least 2025-06 (`git log -L`). URL built at `aosdl.py:386-389`:
  `http://10.46.4.37/bop/images/OS_<major with . replaced by _>_<build>_<release>/<family>/Release/<Img>`,
  e.g. `.../OS_8_11_162_R01/shasta/Release/Uos.img`.
- **Protocol and direction:** the **switch pulls** over plain HTTP. tsbuddy SSHes to the switch and types
  `curl -kL "<url>" --output /flash/<folder>/<img>` into the switch's shell (`aosdl.py:391`, or `/flash/<img>` with no
  folder, `aosdl.py:397`). Nothing is pushed from the machine running tsbuddy.
- **Credentials:** none for the image server (no auth in the URL or in curl). The switch login is prompted
  (`input` for the user, default `admin`; `getpass`/`safe_password_prompt` for the password, default `switch`;
  `aosdl.py:284-285, 334-335`). There are no env vars or config file for the AOS tools. `~/.tsbuddy_secrets`
  (`utils/ale_auth.py:7`) holds only a GitHub token for upgrading tsbuddy itself from the private repo, which is unrelated.
- **Reachability:** *verifiable*: 10.46.4.37 is an RFC 1918 address, so it is not on the public internet. *Inferred*: it is
  an ALE-internal build or image server (`/bop/images`), reachable from ALE's corporate network or VPN. The CHANGELOG
  notes for other private features "requires network access to the CMDS host (corporate network or VPN)"
  (`CHANGELOG.md:16`). Whether the **lab switch network** routes to it is unknown. tsbuddy's design assumes it does,
  because the switch runs curl, but that is the maintainer's lab, not necessarily BLab's. This was not tested (no
  traffic was sent).

## 2. Driving it as a library; how the upgrade works

**Entry points** (`pyproject.toml:30-32`): `aosdl = tsbuddy.aos.aosdl:main`, `aosga = tsbuddy.aos.aosdl:lookup_ga_build`,
`aosup = tsbuddy.aos.aosdl:aosup`. `tsbuddy` (the menu, `tsbuddy_menu.py`) prints at import time and runs a version check
(`tsbuddy_menu.py:1,15,19`). Do not import it.

| Function | Signature | Interactive? |
|---|---|---|
| `lookup_ga_build()` | no args | loops on `input()` (`:276,300,304`), `getpass` (`:285`); calls `aosup()` |
| `aosup(found_ga_build=None)` | | `input()` for the folder (default `working`) and for reload y/n (`:354-355`), then `main()` |
| `main(folder_name=None, reload_when_finished=False, found_ga_build=None, hosts=None)` | | **no prompts if both `found_ga_build` and `hosts` are given** (`:461-471`); otherwise `get_aos_version_simple()` / `collect_hosts()` prompt |
| `download_images_for_host(host, aos_major, aos_build, aos_release, image_map, base_ip, base_dir, folder_name=None, reload_when_finished=False)` | `host = {"ip","username","password"}` | none; lowest-level usable function (`:367`) |
| `get_ga_build(version, family)` | `version` like `"8.10R02"` or `"latest"` | none; returns `".105"` (suffix only) or the full `"8.11.162.R01"` for `latest` (`:243-259`) |
| `get_family_from_ip(host)` | | none; SSH + `su` (`:428-457`) |

`main(..., found_ga_build="8.11.162.R01", hosts=[{...}])` is the only prompt-free path. It returns `None`, reports only
via `print`, and catches every exception (`:409-410`).

**Upgrade sequence** (`download_images_for_host`, `aosdl.py:367-415`), using paramiko `invoke_shell()` with
`AutoAddPolicy` (`:372-375`):

1. `su` to enter the switch's Linux shell (`:376`, comment: "No password needed").
2. `echo $PS1`. The family is the first word of the prompt, lowercased (`get_platform_family`, `:341-349`). The model is not
   used here.
3. For each image of the family (`image_map`, `:10-28`): `curl -kL "<url>" --output /flash/<folder>/<img>` (`:391`).
   Completion is detected by `wait_for_shell` (`:141-155`): it reads until a chunk contains `"# "`, `"#->"`, `"$ "`, `">"`
   or `"login: "`, **or 200 s pass** (raised from 120 s for the larger 8.10R04 images, commit `a7fcd86`). The curl output
   is ignored (`download_output` is unused), and nothing checks exit status, size, checksum, free space or that the
   directory exists (no `mkdir`).
4. If `reload_when_finished and folder_name`: `exit` (leave `su`), `sleep 4`, then
   `echo 'y' | reload from <folder> no rollback-timeout`, `sleep 4`, close (`:402-408`).
5. Nothing waits for the switch to come back, and nothing verifies the running version afterwards.

**Directories:** any `/flash/<folder>/`. The default is `working` (`:354`). `certified` is never written, and there is no
`copy running certified`. An `init` folder would work the same way (`reload from init`), but tsbuddy neither creates it
nor seeds it with config. **Inferred risk:** if a download outlasts 200 s, the script carries on, queues `exit` and
`reload` behind the running curl, then closes the SSH session 4 s later, which may kill curl and leave a truncated image.

## 3. Compatible releases per model; latest GA per family

- **Model → family:** `model_family_map` (`aosdl.py:30-45`), keyed by bare model numbers: `6360 aravalli`, `6370 horsley`,
  `6465 vindhya`, `6560 nandi`, `6570M whitney`, `6575 dronagiri`, `6860 shasta`, `6860N shasta_n`, `6865 everest`,
  `6870 kailash`, `6900 Tor tor`, `6900 Yukon yukon`, `6920 yukon_plus`, `9900 medora`. OS6900 is split by variant
  (`model_6900_variants`, `:47-51`: Yukon = T48C6/V72/V48C8/X48C6, Tor = X20/X40/X72/T20/T40/Q32). BLab stores models
  like `OS6860-48` (`api/api/fake_devices.py:224`), so it needs to normalise them (strip `OS`, take the suffix, and tell
  6860 from 6860N and the 6900 variants apart).
- **Family → image file:** `image_map` (`aosdl.py:10-28`), e.g. shasta/everest `Uos.img`, shasta_n `Uosn.img`,
  yukon `Yos.img`, yukon_plus `Ypos.img`, tor `Tos.img`, vindhya/nandi `Nos.img`, aravalli `Nosa.img`, whitney `Wos.img`,
  kailash `Kaos.img`, dronagiri `Dos.img`, horsley `Hos.img`, medora `Mos.img`+`Meni.img`+`Mhost.img` (three files). A
  second, inconsistent copy, `family_model_image_map` (`:53-110`), lists only `Mos.img` for medora and is unused.
- **Compatible releases:** `ga_index.json` maps release (24 keys, `8.11R01` down to `8.3.1R01`) → family → build suffix
  (`".162"`). `""`, `"N/S"` (not supported), `"N/A"` and `"UNK"` mean no GA (`get_ga_build`, `aosdl.py:255`). Releases
  compatible with a model = the keys where that family's value is a real suffix. Full version =
  `<major>` + suffix + `.<R..>`, e.g. `8.10` + `.105` + `.R02` (`aosdl.py:316-318`).
- **Latest GA per family:** `ga_latest.json` gives `latest_version` per family, currently `8.11.162.R01` for all except
  tor (`8.9.94.R04`; Tor is N/S since 8.10, see `ga_index.json` and CHANGELOG 0.0.47).
- Both files are **hand-maintained** (history: 2025-09-19, 2025-10-14, 2026-01-07, 2026-04-22, 2026-09-17). There is no
  API. A GA appears in BLab only once someone updates the JSON and BLab picks it up.
- Known bugs: `get_ga_build("latest", unknown_family)` returns `None` instead of raising (it checks `version`, not
  `build`, at `:247`). The `8.x.1Rxx` keys (`8.4.1R03`, …) would be mis-split by `main()` (`:464-465`).

## 4. License, Python, dependencies, maintenance

- **License:** none. There is no LICENSE file, `"License :: OSI Approved :: MIT License"` is commented out
  (`pyproject.toml:17`), and PyPI metadata `license` is null. By default that means all rights reserved.
- **Python:** declares `requires-python = ">=3.7"` (`pyproject.toml:10`), but `aosdl.py:417,424,428` use `str | None` /
  `list[str]` annotations without `from __future__ import annotations`, so importing it fails below 3.10. **On 3.10 it
  works:** all files compile under 3.10.11. `log_analyzer/logparser_v2.py` uses `match` statements, which are also 3.10+.
  No `tomllib`.
- **Dependencies** (`pyproject.toml:19-24`): `paramiko`, `xlsxwriter`, `scapy`, `pandas>=2.3.1`. pandas (with numpy) and
  scapy are heavy. The `aos` module only needs paramiko.
- **Versions/activity:** PyPI latest is 0.0.46 (2026-09-09). The repo is at 0.0.47 (2026-09-17, unreleased). There are 41
  PyPI releases, 28 commits since 2026-01-01, and one main maintainer ("Brian"/bgbyte) with a few contributors. The
  project is pre-1.0, with no tests in the repo and no tags.

## 5. Risks for BLab

- **Interactive and print-driven:** `input`/`getpass` in every entry point. Results go to stdout, and errors are printed
  and swallowed (`aosdl.py:409-415, 451-457`), so a library caller cannot tell success from failure.
- **Import side effects:** `import tsbuddy.aos` reads two JSON files (harmless). `tsbuddy/__init__.py:1-6` tries
  `from tsbuddy import private_repo`. `tsbuddy_menu` prints and runs a version check. `utils/ale_auth.py` can write
  `~/.tsbuddy_secrets`, write a temp script and `pip uninstall/install` itself (`:61-97`). It is only reached from the
  menu, but it is in the same package.
- **Hard-coded paths:** image host/path (`aosdl.py:468-469`), `/flash/<folder>`, `reload ... no rollback-timeout`.
- **Needs `su`** (a Linux root shell) on the switch for curl and family detection. If `su` is disabled or locked down on
  some release or hardened config, it breaks. *Inferred*, not verified.
- **Bugs:** `safe_password_prompt` calls `getpass.getpass` after `from getpass import getpass` (`aosdl.py:2,115`), which
  always throws and falls back to a visible `input()`. The `get_ga_build` latest check is also wrong (above).
- **SSH host keys:** `AutoAddPolicy` (`:373, 437`).
- **No sudo and no Windows-only code in `aos`.** `stty` is called in the password fallback (`:122,125`, POSIX-only, and
  only when there is no TTY).
- **Upgrade safety:** no free-space check, checksum, timeout handling, wait-for-boot or post-upgrade verification (see §2).
- **Private fork divergence:** an employee-only repo exists (`pyproject.toml:44`, `CHANGELOG.md:3-5`). Its AOS code
  could differ, but it was not inspected.

## Sources

- https://github.com/bgbyte/tsbuddy, commit `7f5245ff6b6cf4e06e8774d83d5fc48192a69fc4`: `pyproject.toml`,
  `src/tsbuddy/aos/aosdl.py`, `src/tsbuddy/aos/ga_index.json`, `src/tsbuddy/aos/ga_latest.json`,
  `src/tsbuddy/aos/__init__.py`, `src/tsbuddy/__init__.py`, `src/tsbuddy/tsbuddy_menu.py`, `src/tsbuddy/utils/ale_auth.py`,
  `CHANGELOG.md`; commit `a7fcd86` (timeout change).
- https://pypi.org/pypi/tsbuddy/json (release list and dates, metadata); wheel `tsbuddy-0.0.46-py3-none-any.whl`.
