# Real regression tests vs. proposed patches (history-real-v1, prompt v7.2 / gate v9, 3 runs per case)

Same 5 cases and method as `history-real-verify-2026-10-23.md` (prompt v7.1 / gate v8). Produced by `python -m evals.history_real_repo verify`.

```
  hist-87a9a4e0: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
  hist-87a9a4e0: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; '_table_with_maxheadercolwidths - As...\n!!!!!!!!!!!!!!!!
  hist-87a9a4e0: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
  hist-92cb7096: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 't_intfmt_with_string_as_integer - AssertionError\n!!!!!!
  hist-92cb7096: patch_error (gate did not accept) - patch does not apply: malformed hunk line: '*** End Patch'
  hist-92cb7096: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 't_intfmt_with_string_as_integer - AssertionError\n!!!!!!
  hist-0978de5b: passes_tests (gate did not accept) - the real regression tests pass; patch is in the real fix's file
  hist-0978de5b: no_patch (gate did not accept) - no patch
  hist-0978de5b: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 'st_of_dataclasses_with_separating_line\n!!!!!!!!!!!!!!!!
  hist-20c6370d: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 'eption_on_empty_data_with_maxcolwidths\n!!!!!!!!!!!!!!!!
  hist-20c6370d: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 'eption_on_empty_data_with_maxcolwidths\n!!!!!!!!!!!!!!!!
  hist-20c6370d: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 'eption_on_empty_data_with_maxcolwidths\n!!!!!!!!!!!!!!!!
  hist-d2eba037: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
  hist-d2eba037: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
  hist-d2eba037: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file

summary: {"fails_tests": 7, "no_patch": 1, "passes_tests": 6, "patch_error": 1}
```
