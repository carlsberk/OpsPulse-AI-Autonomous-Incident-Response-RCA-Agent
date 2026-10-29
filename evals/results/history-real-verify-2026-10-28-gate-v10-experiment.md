# Real regression tests vs. proposed patches, gate v10 experiment (retry once on a surviving copy)

Same 5 cases, 3 runs each, as the 2026-10-23 and 2026-10-27 files. The v10 code is **not** in this repository (it did not help); this is the record of the negative result.

```
  hist-87a9a4e0: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; '_table_with_maxheadercolwidths 
  hist-87a9a4e0: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; '_table_with_maxheadercolwidths 
  hist-87a9a4e0: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; '_table_with_maxheadercolwidths 
  hist-92cb7096: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 't_intfmt_with_string_as_integer
  hist-92cb7096: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 't_intfmt_with_string_as_integer
  hist-92cb7096: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 't_intfmt_with_string_as_integer
  hist-0978de5b: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
  hist-0978de5b: passes_tests (gate did not accept) - the real regression tests pass; patch is in the real fix's file
  hist-0978de5b: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 'st_of_dataclasses_with_separati
  hist-20c6370d: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
  hist-20c6370d: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 'eption_on_empty_data_with_maxco
  hist-20c6370d: patch_error (gate did not accept) - patch does not apply: malformed hunk line: '*** End Patch ***'
  hist-d2eba037: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
  hist-d2eba037: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
  hist-d2eba037: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file

summary: {"fails_tests": 8, "passes_tests": 6, "patch_error": 1}
```
