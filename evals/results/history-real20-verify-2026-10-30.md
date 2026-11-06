# 20 real bug-fix cases, prompt v7.2 / gate v9: patches vs. the real regression tests

Cases from 6 pure-Python libraries (python-tabulate 4, more-itertools 6, boltons 6, markdown-it-py 2, schema 1, toolz 1), 2 runs each; the 16 cases whose first runs hit connection errors were run again and merged (same versions). Verified in the no-network Docker container (`history_real_repo verify`).

```
    hist-87a9a4e0: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; '_table_with_maxheadercolwidths - As...\n!!!!!!!!!
    hist-87a9a4e0: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; '_table_with_maxheadercolwidths - As...\n!!!!!!!!!
    hist-92cb7096: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 't_intfmt_with_string_as_integer - AssertionError\
    hist-92cb7096: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-def2d821: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-def2d821: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-d81871b8: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-d81871b8: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-0978de5b: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-0978de5b: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-20c6370d: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-20c6370d: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-d2eba037: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-d2eba037: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-2ecf547b: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-2ecf547b: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-4e5faa3d: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 'ytesIO::test_buffer_write_rollover_uses_byte_coun
    hist-4e5faa3d: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 'ytesIO::test_buffer_write_rollover_uses_byte_coun
    hist-73e22c90: no_patch (gate did not accept) - no patch
    hist-73e22c90: no_patch (gate did not accept) - no patch
    hist-970051b6: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 'est_empty_field_name_raises_value_error[namedlist
    hist-970051b6: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 'est_empty_field_name_raises_value_error[namedlist
    hist-f1034b07: no_patch (gate did not accept) - no patch
    hist-f1034b07: no_patch (gate did not accept) - no patch
    hist-435774ef: no_patch (gate did not accept) - no patch
    hist-435774ef: no_patch (gate did not accept) - no patch
    hist-1b7ef656: patch_error (gate did not accept) - patch does not apply: hunk 1: context/removed lines were not found in the source (first line: '    maximum = state.
    hist-1b7ef656: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; '5_table_in_blockquote_at_eof_html_block\n!!!!!!!!
    hist-53ca3e9c: patch_error (gate did not accept) - patch does not apply: hunk 1: context/removed lines were not found in the source (first line: '    try:'). The hunk
    hist-53ca3e9c: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-f51a53bf: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-f51a53bf: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-edb3346f: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-edb3346f: passes_tests (gate accepted) - the real regression tests pass; patch is in the real fix's file
    hist-be5793a5: fails_tests (gate accepted) - the real regression tests still fail; patch is in the real fix's file; 'epeat_with_iterators - ...\n!!!!!!!!!!!!!!!!!!!!!
    hist-be5793a5: no_patch (gate did not accept) - the run has no single-file patch
    hist-06f31819: passes_tests (gate did not accept) - the real regression tests pass; patch is in the real fix's file
    hist-06f31819: no_patch (gate did not accept) - no patch
    hist-d9adeb4e: no_patch (gate did not accept) - no patch
    hist-d9adeb4e: no_patch (gate did not accept) - no patch
  
  summary: {"fails_tests": 9, "no_patch": 10, "passes_tests": 19, "patch_error": 2}
```

Cross-check: the same container re-verified the 15 runs verified on the host on 2026-10-27; all 15 verdicts were identical, so host and container agree and the run-to-run differences below are the model's sampling.
