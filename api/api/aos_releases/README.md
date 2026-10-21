# AOS release data, from tsbuddy

`ga_latest.json` (the latest GA release per AOS family) and `ga_index.json` (the GA build of each release per family) are copied from tsbuddy by Brian (ALE US team), with his OK: <https://github.com/bgbyte/tsbuddy>, `src/tsbuddy/aos/`, commit `7f5245f` (2026-09-17). Its model -> family and family -> image tables are in `../aos_release.py`.

To follow a new GA, copy tsbuddy's two files over these, unchanged. Each Switch moves to the new release at its next Cleanup (docs/adr/0011).
