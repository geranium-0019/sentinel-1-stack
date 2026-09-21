# Orbit downloader validation — 2026-09-17

Image: `sentinel-1-stack:dev`, SHA256 `631b73c4d46ab4b9b859f041ef5273cf3c2df5bb6f545aa28367fe06fd6ff335`.
No dependencies were added or upgraded. Existing ISCE2 / Conda build layers were reused.

- 66 combined offline tests passed. Orbit cases cover S1D scene discovery, ZIP/SAFE deduplication, incomplete SLC exclusion, precise-only policy, explicit restituted permission, full-interval coverage with a 60-second margin, existing-file reuse, corrupt/symlink preservation, shared-orbit deduplication, XML validation, missing state vectors, interrupted transfers, DNS retries, and custom config paths.
- The host launcher was exercised against the actual Anak configuration with `--dry-run --allow-restituted`. At that time five completed ZIPs were present and one `.part` was excluded. No EOF or log files were created by this check.
- Real ASF transfers were exercised inside a temporary container directory, using scene-name fixtures rather than copying multi-gigabyte SLCs. All three EOFs passed XML validation and were then reused without changing their contents or timestamps:
  - S1A 2017-12-02: `S1A_OPER_AUX_POEORB_OPOD_20210305T090710_V20171201T225942_20171203T005942.EOF`
  - S1D 2026-08-22: `S1D_OPER_AUX_POEORB_OPOD_20260911T071426_V20260821T225942_20260823T005942.EOF`
  - S1D 2026-09-03: `S1D_OPER_AUX_RESORB_OPOD_20260903T134217_V20260903T094604_20260903T132104.EOF`
- The precise-only run stopped before writing any EOF when the final scene required RESORB. Explicit `--allow-restituted` succeeded.
- ISCE2 2.6.5 `Sentinel1.extractPreciseOrbit` read each downloaded EOF and extracted 15 state vectors around the acquisition interval. This validates orbit reading, not the full S1D SAR processing workflow.
- Requests used the ASF scene API and public S3 with `RES_OPTIONS=edns0`, without mounting Earthdata credentials. The temporary files were removed on test completion; the actual project's orbit directory was not populated.

One full-suite invocation exited with code 139 and no output while the real-network smoke test was also running. Its cause is undetermined. Subsequent diagnostic runs with Python faulthandler passed all 66 tests, both with the launcher's thread limits (`OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=2`) and with default thread settings. This observation is not claimed to be fixed by the orbit implementation.

Reference implementation: `/home/gray/master/sbas_test/SBAS/workdir/tools/fetchOrbit_asf.py` (read-only reference; unmodified).
The service endpoint and public S3 mechanism were checked against [ASF's official client](https://github.com/ASFHyP3/sentinel1-orbits-py/blob/main/src/s1_orbits/s1_orbits.py).

Offline reproduction:

```bash
docker run --rm \
  --env OPENBLAS_NUM_THREADS=1 --env OMP_NUM_THREADS=2 \
  --mount type=bind,source="$PWD",target=/project,readonly \
  --workdir /project \
  sentinel-1-stack:dev python -X faulthandler -m unittest discover -s tests -v
```
