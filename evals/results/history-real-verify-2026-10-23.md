# Real regression tests vs. proposed patches (history-real-v1, prompt v7.1, 3 runs per case)

Produced by `python -m evals.history_real_repo verify`: each patch is applied to the parent of a real bug-fix commit and the test that fix added is run. 5 cases (4 from astanin/python-tabulate, 1 from pytoolz/toolz). Raw per-run lines:

```
  hist-87a9a4e0: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
  hist-87a9a4e0: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; '_table_with_maxheadercolwidths - As...\n!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failure
  hist-87a9a4e0: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; '_table_with_maxheadercolwidths - As...\n!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failure
  hist-92cb7096: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 't_intfmt_with_string_as_integer - AssertionError\n!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after
  hist-92cb7096: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 't_intfmt_with_string_as_integer - AssertionError\n!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after
  hist-92cb7096: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 't_intfmt_with_string_as_integer - AssertionError\n!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after
  hist-0978de5b: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 'st_of_dataclasses_with_separating_line\n!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failure
  hist-0978de5b: patch_error (gate did not accept) - patch does not apply: hunk 1: context/removed lines were not found in the source (first line: '        field_names = [field.name for field in 
  hist-0978de5b: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
  hist-20c6370d: no_patch (gate did not accept) - no patch
  hist-20c6370d: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 'eption_on_empty_data_with_maxcolwidths\n!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failure
  hist-20c6370d: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 'eption_on_empty_data_with_maxcolwidths\n!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failure
  hist-d2eba037: patch_error (gate did not accept) - patch does not apply: hunk without any added or removed line
  hist-d2eba037: no_patch (gate did not accept) - no patch
  hist-d2eba037: no_patch (gate did not accept) - no patch

summary: {"fails_tests": 8, "no_patch": 3, "passes_tests": 2, "patch_error": 2}
```
