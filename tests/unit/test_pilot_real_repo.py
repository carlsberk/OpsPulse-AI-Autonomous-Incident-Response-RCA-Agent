"""The pilot's mutation table must stay importable and well-formed (it broke once when a field was inserted mid-way)."""

from evals.pilot_real_repo import MUTATIONS, Edit
from src.agent.schemas import ROOT_CAUSE_CATEGORIES


def test_every_mutation_is_well_formed():
    assert len(MUTATIONS) >= 4 and len({m.id for m in MUTATIONS}) == len(MUTATIONS)
    for mutation in MUTATIONS:
        assert mutation.category in ROOT_CAUSE_CATEGORIES
        assert all(alt in ROOT_CAUSE_CATEGORIES and alt != mutation.category for alt in mutation.alternatives)
        assert mutation.fix_files and all(isinstance(path, str) for path in mutation.fix_files)
        assert mutation.edits and all(isinstance(edit, Edit) for edit in mutation.edits)
        assert isinstance(mutation.trigger, str)
