# DEM downloader validation — 2026-09-17

Image: `sentinel-1-stack:dev`, SHA256 `33523bff8d67028b56ca7a78c50c2cd4cce171a93060e05df2e23f1b0aaae081`.
No packages were installed, upgraded, or removed. The existing ISCE2 and numerical build layers were reused.

## Confirmed behavior

- The actual six Anak S1D ZIP manifests were read without extracting measurement TIFFs. Union SNWE: `[-7.692004, -5.340134, 103.508125, 106.171051]`. With the default 0.1-degree margin, outward-rounded bounds are `[-8, -5, 103, 107]`, requiring 12 tiles.
- The host launcher's dry-run successfully queried ESA's SRTMGL1 listing and reported four unavailable tiles: `S08E103`, `S08E104`, `S07E103`, `S07E104`. It created no DEM, cache, or logs in the real project. DNS retries were observed and recovered.
- A real `S07E105` tile was downloaded and processed with the installed `dem.py`, using no missing-data fill. WGS84 ellipsoid output dimensions were 3600×3600, 25,920,000 bytes. Metadata, bounds, GDAL raster reads, publication, and unchanged-file reuse passed.
- Full-extent smoke testing copied only the six real manifests into filename fixtures in a temporary container directory. With explicit `--fill-missing-zero`, eight real tiles were downloaded and the four unavailable tiles were generated at zero EGM96 height. The installed `dem.py` stitched the tiles and converted their vertical reference to WGS84. The validated result was 14400×10800 pixels, 311,040,000 bytes. The record identified all four filled tiles (12,967,201 input samples each) and no internal voids in the eight downloaded tiles. Reuse succeeded.
- All smoke-test outputs were temporary and removed after verification. No DEM was published into the user's actual Anak project, and the user's YAML was not changed. Treat zero-fill as an explicit user choice, not as proof that an unavailable tile contains only ocean.

## Tests and unresolved environment behavior

- 20 DEM-specific tests passed in the Docker image on an isolated run. They cover footprints, coordinate order, union and negative-coordinate rounding, ZIP/SAFE discovery, invalid coordinates, unsupported extents, dry-run writes, explicit fill, HTTP failures, corrupt ZIP/cache handling, void pixels, interrupted downloads, publication/reuse, metadata relocation, and checksum tampering.
- A stable host run passed 35 relevant tests (DEM, launcher, config, workspace):

  ```bash
  PYTHONPATH=src:tests /usr/bin/python3 -m unittest test_dem test_launcher test_config test_workspace -q
  ```

- The combined 87-test Docker run is **not verified as passing**. Failures were irregular: one run raised `RuntimeError: invalid RE code` while importing the existing ASF/dateparser/regex dependencies; others segfaulted in ElementTree or unittest.mock. Running each test module in a fresh process also produced intermittent failures in existing SLC/orbit tests and in a DEM test's argparse setup, with inconsistent Python object types. A debug allocator did not eliminate the failures. No root cause or environment fix is claimed.
- Related unexplained process failures predate the DEM work (see `orbit-validation.md`). Isolated ASF imports and small regex/dateparser/XML probes passed, so the available evidence does not establish which component is responsible. Dependencies, Docker/WSL configuration, and system DNS were left unchanged.
- Successful real DEM workflows and host unit tests establish functionality for the exercised cases; they do not resolve the image's broader stability problem or validate a complete InSAR pipeline.

## Sources and limits

- [ESA STEP SRTMGL1 distribution](https://step.esa.int/auxdata/dem/SRTMGL1/).
- Installed ISCE2 `applications/dem.py`, `components/contrib/demUtils/DemStitcher.py`, and `Correct_geoid_i2_srtm.py` were inspected to verify local tile names, output dimensions, and the EGM96→WGS84 conversion path.
- The implementation uses the entire scene footprint, not `processing.bbox` or selected subswaths. The SRTM dataset does not represent terrain changes at the SLC acquisition date.
- Dateline crossings, bounds outside −56…60 degrees latitude including margin, and requests above 100 tiles stop with an explicit error. Missing terrain is not inferred to be ocean. An HTTP/DNS/ZIP failure never triggers zero-fill.
