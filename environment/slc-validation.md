# SLC downloader integration validation — 2026-09-17

Validated in the existing Linux/amd64 Docker image, Python 3.11.16.

- Dependency resolver accepted `asf_search==14.0.0` with all existing Python distribution versions constrained.
- Added seven pinned distributions; NumPy 1.26.4, GDAL 3.10.3, SciPy 1.17.1, h5py 3.16.0 and other pre-existing distributions unchanged.
- Both import orders tested: ASF/Shapely before ISCE2/GDAL and vice versa.
- Exercised Shapely and OGR geometry operations, GDAL raster write/read, and imported compiled TOPS/SNAPHU modules.
- `stackSentinel.py -h` succeeded.
- 44 offline tests passed after the config-driven init/download and DNS retry revisions, including fake Earthdata redirects using the real ASFSession, non-destructive initialization, default SLC placement, output override and read-only JSON preservation.
- Config checks cover relative work_dir resolution, custom paths, malformed settings, file conflicts, and symlinks escaping the work root. Host launcher tests cover read-only mounts, absent credentials in dry-run, external output mapping without writes, and failure exit propagation.
- Real Docker launcher smoke test passed from a different current directory, with spaces in paths: init, repeated init preserving an existing ZIP, custom SLC/log directories, actual 8-product GeoJSON dry-run, and an external output override. No working files were created during dry-run; the source JSON hash remained unchanged. Host init was tested in a temporary home directory, not the actual SSD project.
- Actual ASF GeoJSON: dry-run accepted 8 products / 34,542,330,039 bytes; source mounted read-only. No SLC download or JSON archival was performed on real data.

Existing baseline diagnostic (also present before this change):

```text
imagecodecs 2026.3.6 has requirement numpy>=2.0, but you have numpy 1.26.4.
```

No new pip-check diagnostics were introduced. This does not establish compatibility of every image codec or completion of a real SAR workflow.
Full authenticated ASF transfer has not been tested in the integrated image; transfer failure, retry, integrity checks and input preservation were tested with synthetic responses. The integrated downloader no longer moves or archives its input JSON.

Authenticated connection check for the Anak S1D batch reproduced a DNS failure after Earthdata authentication at `dy4owt9f80bz7.cloudfront.net`. With `RES_OPTIONS=edns0`, retrying recovered. The downloader now retries pre-body DNS failures at most three times (1, 2, 4 seconds), with visible messages and no signed URLs in logs. The rebuilt image recovered after one retry and returned HTTP 200 with Content-Length 4,443,642,765 for the first product. The response body was not consumed and no ZIP was saved; this is not a full-transfer or MD5 validation. Offline tests cover recovery, retry exhaustion, interruption during backoff, and non-DNS errors remaining unretried. No WSL/Docker restart or system DNS changes were performed.

Conda pip encountered native crashes during online installation in Docker build. Wheel acquisition now uses a separate Python build stage; final installation uses only local wheels and preserves the numerical environment.

Reproduction commands are in README.md.
