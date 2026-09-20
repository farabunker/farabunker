"""Unit tests for models/queue/scheduler.py -- the pure admission policy.

No DB, no Django, no `@pytest.mark.django_db` -- `scheduler.py` is stdlib +
dataclasses only, and these tests exercise it exactly the way it is meant
to be provable: construct `SchedCandidate`/`SchedModel` snapshots by hand,
call `plan_admissions`, assert on the returned job ids. Table-driven where
a table reads more clearly than a wall of near-identical test methods
(matches `models/registry/tests/test_roles.py`'s pure-registry style,
one rung up the stack).
"""
from __future__ import annotations

import pytest

from models.queue.scheduler import (
    SchedCandidate,
    SchedModel,
    effectively_exclusive,
    plan_admissions,
    resident_bytes,
)

GB = 1024**3


def model(key: str, footprint: int | None) -> SchedModel:
    """Shorthand: a single-segment `key` string standing in for the real
    `(engine, endpoint, model_id)` triple -- this module never inspects
    the triple's internal structure, so a bare string wrapped in a 3-tuple
    keeps every test candidate short and readable."""
    return SchedModel(key=(key, key, key), footprint_bytes=footprint)


def candidate(
    job_id: int,
    *,
    priority: int = 100,
    exclusive: bool = False,
    models: tuple[SchedModel, ...] = (),
) -> SchedCandidate:
    return SchedCandidate(job_id=job_id, priority=priority, exclusive=exclusive, models=models)


# --- priority order / tie-break --------------------------------------------


class TestPriorityOrder:
    def test_lower_priority_number_runs_first(self):
        low = candidate(1, priority=200, models=(model("a", 1 * GB),))
        high = candidate(2, priority=100, models=(model("b", 1 * GB),))

        admitted = plan_admissions([low, high], [], budget_bytes=10 * GB, max_concurrent=1)

        assert admitted == [2]

    def test_fifo_tie_break_by_job_id_within_equal_priority(self):
        second = candidate(20, priority=100, models=(model("a", 1 * GB),))
        first = candidate(10, priority=100, models=(model("b", 1 * GB),))

        admitted = plan_admissions([second, first], [], budget_bytes=10 * GB, max_concurrent=1)

        assert admitted == [10]


# --- sequential mode (budget_bytes is None) --------------------------------


class TestSequentialMode:
    def test_admits_head_only_when_idle(self):
        head = candidate(1, priority=100)
        tail = candidate(2, priority=200)

        admitted = plan_admissions([head, tail], [], budget_bytes=None, max_concurrent=4)

        assert admitted == [1]

    def test_admits_nothing_when_anything_running(self):
        head = candidate(1, priority=100)
        running = [candidate(99, priority=1)]

        admitted = plan_admissions([head], running, budget_bytes=None, max_concurrent=4)

        assert admitted == []

    def test_empty_candidates_admits_nothing(self):
        assert plan_admissions([], [], budget_bytes=None, max_concurrent=4) == []


# --- the headline case: two small jobs both fit ----------------------------


class TestBothFitSameRound:
    def test_two_small_disjoint_jobs_both_admitted(self):
        a = candidate(1, priority=100, models=(model("a", 5 * GB),))
        b = candidate(2, priority=100, models=(model("b", 5 * GB),))

        admitted = plan_admissions([a, b], [], budget_bytes=20 * GB, max_concurrent=4)

        assert admitted == [1, 2]


# --- resident-set dedup by SchedModel.key ----------------------------------


class TestSharedModelDedup:
    def test_same_key_both_fit_under_budget_that_would_reject_double_counting(self):
        a = candidate(1, priority=100, models=(model("shared", 20 * GB),))
        b = candidate(2, priority=100, models=(model("shared", 20 * GB),))

        admitted = plan_admissions([a, b], [], budget_bytes=25 * GB, max_concurrent=4)

        assert admitted == [1, 2]

    def test_different_keys_same_sizes_only_first_admitted(self):
        a = candidate(1, priority=100, models=(model("k1", 20 * GB),))
        b = candidate(2, priority=100, models=(model("k2", 20 * GB),))

        admitted = plan_admissions([a, b], [], budget_bytes=25 * GB, max_concurrent=4)

        assert admitted == [1]

    def test_cross_kind_sharing_second_candidate_excludes_already_counted_key(self):
        a = candidate(1, priority=100, models=(model("chat", 10 * GB), model("embed", 15 * GB)))
        b = candidate(2, priority=100, models=(model("embed", 15 * GB),))

        admitted = plan_admissions([a, b], [], budget_bytes=25 * GB, max_concurrent=4)

        # A's total is already 25GB (10 + 15). B's marginal cost excludes
        # the embed key A already brought resident, so B costs 0 more.
        assert admitted == [1, 2]

    def test_cross_kind_sharing_via_running_job(self):
        running = [candidate(99, priority=1, models=(model("embed", 15 * GB),))]
        b = candidate(2, priority=100, models=(model("embed", 15 * GB), model("chat", 10 * GB)))

        admitted = plan_admissions([b], running, budget_bytes=25 * GB, max_concurrent=4)

        # embed already resident via the running job: b's marginal cost is
        # only its chat model (10GB), which fits in the remaining budget.
        assert admitted == [2]


# --- unknown footprint => effectively exclusive ----------------------------


class TestUnknownFootprintExclusive:
    def test_not_admitted_while_anything_running(self):
        running = [candidate(99, priority=1, models=(model("other", 1 * GB),))]
        unknown = candidate(1, priority=100, models=(model("a", None),))

        admitted = plan_admissions([unknown], running, budget_bytes=10 * GB, max_concurrent=4)

        assert admitted == []

    def test_admitted_alone_when_idle(self):
        unknown = candidate(1, priority=100, models=(model("a", None),))

        admitted = plan_admissions([unknown], [], budget_bytes=10 * GB, max_concurrent=4)

        assert admitted == [1]

    def test_nothing_admitted_after_it_in_same_round(self):
        unknown = candidate(1, priority=100, models=(model("a", None),))
        follower = candidate(2, priority=200, models=(model("b", 1 * GB),))

        admitted = plan_admissions([unknown, follower], [], budget_bytes=10 * GB, max_concurrent=4)

        assert admitted == [1]


class TestRunningUnknownFootprintBlocksEverything:
    def test_running_job_with_unknown_model_blocks_all_admissions(self):
        running = [candidate(99, priority=1, models=(model("a", None),))]
        small = candidate(1, priority=100, models=(model("b", 1 * GB),))

        admitted = plan_admissions([small], running, budget_bytes=100 * GB, max_concurrent=4)

        assert admitted == []


# --- declared-exclusive running job blocks everything (rule 2) -------------


class TestRunningDeclaredExclusiveBlocksEverything:
    def test_running_declared_exclusive_job_blocks_all_admissions(self):
        running = [candidate(99, priority=1, exclusive=True, models=(model("a", 1 * GB),))]
        small = candidate(1, priority=100, models=(model("b", 1 * GB),))

        admitted = plan_admissions([small], running, budget_bytes=100 * GB, max_concurrent=4)

        assert admitted == []


# --- oversize (own dedup'd total exceeds budget) ---------------------------


class TestOversizeExclusive:
    def test_admitted_alone_when_idle_does_not_deadlock(self):
        oversize = candidate(1, priority=100, models=(model("a", 50 * GB),))

        admitted = plan_admissions([oversize], [], budget_bytes=10 * GB, max_concurrent=4)

        assert admitted == [1]

    def test_blocks_followers_in_same_round(self):
        oversize = candidate(1, priority=100, models=(model("a", 50 * GB),))
        follower = candidate(2, priority=200, models=(model("b", 1 * GB),))

        admitted = plan_admissions([oversize, follower], [], budget_bytes=10 * GB, max_concurrent=4)

        assert admitted == [1]

    def test_not_admitted_while_anything_running(self):
        running = [candidate(99, priority=1, models=(model("other", 1 * GB),))]
        oversize = candidate(1, priority=100, models=(model("a", 50 * GB),))

        admitted = plan_admissions([oversize], running, budget_bytes=10 * GB, max_concurrent=4)

        assert admitted == []

    def test_dedup_within_itself_before_comparing_to_budget(self):
        # Same key twice within one candidate's own models: dedup'd own
        # total is 20GB (not 40GB), which fits budget, so it is NOT oversize.
        not_oversize = candidate(
            1, priority=100, models=(model("shared", 20 * GB), model("shared", 20 * GB))
        )

        admitted = plan_admissions([not_oversize], [], budget_bytes=25 * GB, max_concurrent=4)

        assert admitted == [1]
        assert effectively_exclusive(not_oversize, budget_bytes=25 * GB) is None


# --- strict order: no backfill past a blocked head -------------------------


class TestStrictOrderNoBackfill:
    def test_big_head_that_does_not_fit_blocks_small_follower_that_would_fit(self):
        # `big` is NOT oversize on its own (5GB <= 10GB budget), so this
        # is not the rule-5 "admit alone when idle" carve-out -- it is
        # blocked purely because 8GB is already resident from a running
        # job, leaving only 2GB of headroom for its 5GB marginal cost.
        # `small` (1GB) WOULD fit in that same 2GB of headroom if tried,
        # but rule 7 forbids trying it once the head is blocked.
        running = [candidate(99, priority=1, models=(model("r", 8 * GB),))]
        big = candidate(1, priority=100, models=(model("big", 5 * GB),))
        small = candidate(2, priority=200, models=(model("small", 1 * GB),))

        admitted = plan_admissions([big, small], running, budget_bytes=10 * GB, max_concurrent=4)

        assert admitted == []
        assert effectively_exclusive(big, budget_bytes=10 * GB) is None


# --- max_concurrent cap -----------------------------------------------------


class TestMaxConcurrentCap:
    def test_cap_enforced_against_idle_machine(self):
        a = candidate(1, priority=100, models=(model("a", 1 * GB),))
        b = candidate(2, priority=200, models=(model("b", 1 * GB),))

        admitted = plan_admissions([a, b], [], budget_bytes=100 * GB, max_concurrent=1)

        assert admitted == [1]

    def test_cap_enforced_with_running_jobs_counted(self):
        running = [candidate(99, priority=1, models=(model("r", 1 * GB),))]
        a = candidate(1, priority=100, models=(model("a", 1 * GB),))

        admitted = plan_admissions([a], running, budget_bytes=100 * GB, max_concurrent=1)

        assert admitted == []

    def test_zero_cap_admits_nothing(self):
        a = candidate(1, priority=100, models=(model("a", 1 * GB),))

        assert plan_admissions([a], [], budget_bytes=100 * GB, max_concurrent=0) == []

    def test_negative_cap_admits_nothing(self):
        a = candidate(1, priority=100, models=(model("a", 1 * GB),))

        assert plan_admissions([a], [], budget_bytes=100 * GB, max_concurrent=-1) == []

    def test_zero_cap_admits_nothing_sequential_mode(self):
        a = candidate(1, priority=100)

        assert plan_admissions([a], [], budget_bytes=None, max_concurrent=0) == []


# --- empty candidates / empty running edge cases ---------------------------


class TestEmptyEdgeCases:
    def test_empty_candidates_budgeted_mode(self):
        assert plan_admissions([], [], budget_bytes=10 * GB, max_concurrent=4) == []

    def test_empty_candidates_with_running_jobs(self):
        running = [candidate(99, priority=1, models=(model("a", 1 * GB),))]

        assert plan_admissions([], running, budget_bytes=10 * GB, max_concurrent=4) == []

    def test_empty_running_is_not_treated_as_exclusive(self):
        a = candidate(1, priority=100, models=(model("a", 1 * GB),))

        assert plan_admissions([a], [], budget_bytes=10 * GB, max_concurrent=4) == [1]


# --- defensive sort ----------------------------------------------------------


class TestDefensiveSort:
    def test_unsorted_input_matches_sorted_input(self):
        a = candidate(1, priority=200, models=(model("a", 1 * GB),))
        b = candidate(2, priority=100, models=(model("b", 1 * GB),))
        c = candidate(3, priority=100, models=(model("c", 1 * GB),))

        unsorted_order = plan_admissions([a, b, c], [], budget_bytes=100 * GB, max_concurrent=4)
        sorted_order = plan_admissions([b, c, a], [], budget_bytes=100 * GB, max_concurrent=4)

        assert unsorted_order == sorted_order == [2, 3, 1]


# --- empty models tuple: treated as unknown/exclusive (DECIDE) -------------


class TestEmptyModels:
    def test_effectively_exclusive_treats_no_models_as_exclusive(self):
        bare = candidate(1, priority=100, models=())

        assert effectively_exclusive(bare, budget_bytes=10 * GB) == "unmeasured model"

    def test_admitted_alone_when_idle_not_free_concurrency(self):
        bare = candidate(1, priority=100, models=())
        follower = candidate(2, priority=200, models=(model("b", 1 * GB),))

        admitted = plan_admissions([bare, follower], [], budget_bytes=10 * GB, max_concurrent=4)

        assert admitted == [1]

    def test_not_admitted_while_anything_running(self):
        running = [candidate(99, priority=1, models=(model("other", 1 * GB),))]
        bare = candidate(1, priority=100, models=())

        admitted = plan_admissions([bare], running, budget_bytes=10 * GB, max_concurrent=4)

        assert admitted == []


# --- helper exports: effectively_exclusive / resident_bytes ----------------


class TestEffectivelyExclusiveHelper:
    def test_declared_exclusive_is_exclusive_regardless_of_footprint(self):
        c = candidate(1, priority=100, exclusive=True, models=(model("a", 1 * GB),))

        assert effectively_exclusive(c, budget_bytes=100 * GB) == "declared"

    def test_unknown_footprint_is_exclusive(self):
        c = candidate(1, priority=100, models=(model("a", None),))

        assert effectively_exclusive(c, budget_bytes=100 * GB) == "unmeasured model"

    def test_oversize_is_exclusive(self):
        c = candidate(1, priority=100, models=(model("a", 50 * GB),))

        assert effectively_exclusive(c, budget_bytes=10 * GB) == "exceeds memory budget"

    def test_small_known_footprint_is_not_exclusive(self):
        c = candidate(1, priority=100, models=(model("a", 1 * GB),))

        assert effectively_exclusive(c, budget_bytes=10 * GB) is None

    def test_no_budget_never_makes_a_sized_candidate_oversize(self):
        c = candidate(1, priority=100, models=(model("a", 999 * GB),))

        assert effectively_exclusive(c, budget_bytes=None) is None

    def test_reason_is_truthy_not_none_is_falsy(self):
        # Every non-None reason string is truthy, and `None` is falsy --
        # `plan_admissions` relies on this at every `if effectively_exclusive(...)`
        # / `any(...)` call site instead of an explicit `is not None` check.
        declared = candidate(1, priority=100, exclusive=True, models=(model("a", 1 * GB),))
        fine = candidate(2, priority=100, models=(model("a", 1 * GB),))

        assert bool(effectively_exclusive(declared, budget_bytes=10 * GB)) is True
        assert bool(effectively_exclusive(fine, budget_bytes=10 * GB)) is False


class TestResidentBytesHelper:
    def test_empty_running_is_zero(self):
        assert resident_bytes([]) == 0

    def test_sums_distinct_keys_across_running_jobs(self):
        running = [
            candidate(1, priority=1, models=(model("a", 5 * GB),)),
            candidate(2, priority=1, models=(model("b", 7 * GB),)),
        ]

        assert resident_bytes(running) == 12 * GB

    def test_dedups_shared_key_across_running_jobs(self):
        running = [
            candidate(1, priority=1, models=(model("shared", 5 * GB),)),
            candidate(2, priority=1, models=(model("shared", 5 * GB),)),
        ]

        assert resident_bytes(running) == 5 * GB

    def test_unknown_footprint_model_contributes_zero(self):
        running = [candidate(1, priority=1, models=(model("a", None),))]

        assert resident_bytes(running) == 0


# --- review round: SchedModel construction guards (m8) ----------------------


class TestSchedModelValidation:
    def test_negative_footprint_bytes_raises(self):
        with pytest.raises(ValueError):
            SchedModel(key=("a", "a", "a"), footprint_bytes=-1)

    def test_none_footprint_is_allowed(self):
        SchedModel(key=("a", "a", "a"), footprint_bytes=None)  # no raise

    def test_zero_footprint_is_allowed(self):
        SchedModel(key=("a", "a", "a"), footprint_bytes=0)  # no raise


# --- review round: candidates/running disjointness precondition (m8) -------


class TestPlanAdmissionsPreconditions:
    def test_job_id_in_both_candidates_and_running_raises(self):
        overlapping = candidate(1, priority=100, models=(model("a", 1 * GB),))
        running = [candidate(1, priority=1, models=(model("a", 1 * GB),))]

        with pytest.raises(AssertionError):
            plan_admissions([overlapping], running, budget_bytes=10 * GB, max_concurrent=4)


# --- review round: MAJOR 1 regression -- divergent footprints reconciled ---


class TestDivergentFootprintReconciliation:
    def test_stale_running_stamp_reconciled_to_larger_candidate_reading(self):
        # The reviewer's exact counterexample for MAJOR 1: a running job's
        # stale, pre-override footprint stamp for key "x" (1GB) must not
        # let a candidate sharing "x" at its TRUE, larger size (20GB) be
        # treated as if only 1GB were really resident. Without max-fold
        # reconciliation, both candidate A (x=20GB, y=4GB) and candidate B
        # (z=20GB, unrelated key) would be admitted against a stale
        # tracked total of only 1(x, stale)+4(y)+20(z)=25GB<=25GB, while
        # the REAL residency would be 20(x, true)+4(y)+20(z)=44GB -- a
        # budget invariant violation. With reconciliation, A's marginal
        # cost correctly reconciles x to 20GB (marginal 19+4=23,
        # used=1+23=24<=25 -> admitted), leaving no room for B's 20GB
        # (24+20=44>25 -> blocked). Real residency stays 20+4=24<=25.
        running = [candidate(99, priority=1, models=(model("x", 1 * GB),))]
        a = candidate(1, priority=100, models=(model("x", 20 * GB), model("y", 4 * GB)))
        b = candidate(2, priority=200, models=(model("z", 20 * GB),))

        admitted = plan_admissions([a, b], running, budget_bytes=25 * GB, max_concurrent=4)

        assert admitted == [1]

    def test_dedup_bytes_max_folds_divergent_readings_for_one_key(self):
        # Direct unit check on the fold itself: two known readings for the
        # same key dedup to their MAX, not the first one seen.
        from models.queue.scheduler import _dedup_bytes

        by_key, has_unknown = _dedup_bytes([model("x", 1 * GB), model("x", 20 * GB)])

        assert by_key == {("x", "x", "x"): 20 * GB}
        assert has_unknown is False


# --- review round: duplicate key + None, order independence (m6) -----------


class TestDuplicateKeyOrderIndependence:
    def test_known_then_unknown_reading_is_unmeasured(self):
        c = candidate(1, priority=100, models=(model("x", 8 * GB), model("x", None)))

        assert effectively_exclusive(c, budget_bytes=100 * GB) == "unmeasured model"

    def test_unknown_then_known_reading_is_unmeasured_same_result(self):
        c = candidate(1, priority=100, models=(model("x", None), model("x", 8 * GB)))

        assert effectively_exclusive(c, budget_bytes=100 * GB) == "unmeasured model"


# --- review round: exact boundaries (missing row 2) -------------------------


class TestExactBoundaries:
    def test_used_plus_marginal_equal_to_budget_admits(self):
        a = candidate(1, priority=100, models=(model("a", 10 * GB),))

        admitted = plan_admissions([a], [], budget_bytes=10 * GB, max_concurrent=4)

        assert admitted == [1]

    def test_own_total_equal_to_budget_is_not_oversize(self):
        c = candidate(1, priority=100, models=(model("a", 10 * GB),))

        assert effectively_exclusive(c, budget_bytes=10 * GB) is None


# --- review round: zero-byte footprints + zero budget (missing row 3) ------


class TestZeroByteFootprintsZeroBudget:
    def test_zero_footprint_fits_zero_budget(self):
        a = candidate(1, priority=100, models=(model("a", 0),))

        admitted = plan_admissions([a], [], budget_bytes=0, max_concurrent=4)

        assert admitted == [1]

    def test_cap_still_binds_at_zero_budget(self):
        a = candidate(1, priority=100, models=(model("a", 0),))
        b = candidate(2, priority=200, models=(model("b", 0),))

        admitted = plan_admissions([a, b], [], budget_bytes=0, max_concurrent=1)

        assert admitted == [1]


# --- review round: negative-headroom safety (missing row 4) -----------------


class TestNegativeHeadroomSafety:
    def test_budget_lowered_below_current_residency_blocks_zero_marginal_candidate(self):
        # Neither running job's OWN total (8GB each) exceeds the new,
        # lower budget (10GB) individually, so rule 3's oversize
        # short-circuit does not fire -- but their COMBINED resident
        # total (16GB) already exceeds it (the operator lowered the
        # budget setting after these jobs were admitted under a higher
        # one). A candidate whose only model is already fully resident
        # (marginal 0) must still be blocked: `used + marginal` is
        # compared directly (16 + 0 = 16 > 10), never `budget - used`
        # (which would go negative and silently look like headroom).
        running = [
            candidate(90, priority=1, models=(model("x", 8 * GB),)),
            candidate(91, priority=1, models=(model("y", 8 * GB),)),
        ]
        zero_marginal = candidate(1, priority=100, models=(model("x", 8 * GB),))

        admitted = plan_admissions([zero_marginal], running, budget_bytes=10 * GB, max_concurrent=4)

        assert admitted == []


# --- review round: running-job oversize equivalence edge (missing row 5) ---


class TestRunningOversizeEquivalenceEdge:
    def test_running_oversize_blocks_small_unrelated_candidate(self):
        # The running job is oversize under the CURRENT budget (its own
        # 50GB > 10GB) -- rule 3's broad predicate blocks everything,
        # even a small, unrelated candidate that would trivially fit on
        # its own numbers (1GB <= 10GB).
        running = [candidate(99, priority=1, models=(model("big", 50 * GB),))]
        small = candidate(1, priority=100, models=(model("small", 1 * GB),))

        admitted = plan_admissions([small], running, budget_bytes=10 * GB, max_concurrent=4)

        assert admitted == []
        assert effectively_exclusive(running[0], budget_bytes=10 * GB) == "exceeds memory budget"


# --- review round: declared-exclusive through plan_admissions (row 8) ------


class TestDeclaredExclusiveThroughPlanAdmissions:
    def test_declared_exclusive_head_admitted_alone_when_idle(self):
        head = candidate(1, priority=100, exclusive=True, models=(model("a", 1 * GB),))
        follower = candidate(2, priority=200, models=(model("b", 1 * GB),))

        admitted = plan_admissions([head, follower], [], budget_bytes=10 * GB, max_concurrent=4)

        assert admitted == [1]

    def test_declared_exclusive_behind_admitted_peer_is_blocked(self):
        first = candidate(1, priority=100, models=(model("a", 1 * GB),))
        exclusive_second = candidate(2, priority=200, exclusive=True, models=(model("b", 1 * GB),))

        admitted = plan_admissions([first, exclusive_second], [], budget_bytes=10 * GB, max_concurrent=4)

        assert admitted == [1]


# --- review round: chained intra-round dedup (missing row 1) ---------------


class TestChainedIntraRoundDedup:
    def test_chain_of_pairwise_shared_keys_all_fit_via_dedup(self):
        # A={x10}, B={x10,y5}, C={y5,z5}, budget 20 -- each pair shares one
        # key with its neighbor, so the true total (x10+y5+z5=20) fits
        # even though the naive (non-dedup'd) sum (10+15+10=35) would not.
        a = candidate(1, priority=100, models=(model("x", 10 * GB),))
        b = candidate(2, priority=200, models=(model("x", 10 * GB), model("y", 5 * GB)))
        c = candidate(3, priority=300, models=(model("y", 5 * GB), model("z", 5 * GB)))

        admitted = plan_admissions([a, b, c], [], budget_bytes=20 * GB, max_concurrent=4)

        assert admitted == [1, 2, 3]


# --- review round: late high-priority arrival (missing row 10) -------------


class TestLateHighPriorityArrivalWithRunningJobs:
    def test_new_low_priority_number_wins_the_freed_slot_over_older_higher_number(self):
        # p=10 "arrives" (is present in this round's candidate set) after
        # a p=100 job has already been sitting in the queue; by the time a
        # running slot frees (`running` is empty this round), p=10 is
        # admitted first -- `plan_admissions` has no memory of "who queued
        # first" beyond `(priority, job_id)`, so the fresher, more urgent
        # arrival always wins the next slot. The owner's verbatim
        # requirement.
        already_queued = candidate(2, priority=100, models=(model("b", 1 * GB),))
        late_arrival = candidate(3, priority=10, models=(model("c", 1 * GB),))

        admitted = plan_admissions(
            [already_queued, late_arrival], [], budget_bytes=10 * GB, max_concurrent=1
        )

        assert admitted == [3]


# --- review round: drain loop, direct proof of invariants (missing row 9) --


class TestDrainLoop:
    def test_drain_loop_admits_every_job_and_never_exceeds_budget(self):
        # Retires only the LOWEST-id running job each round (not the whole
        # running set) so successive rounds interleave still-running work
        # with fresh admissions -- the mixed running+candidates regime is
        # exactly where MAJOR 1 (divergent-footprint over-admission) lived,
        # so this is the regime the drain proof needs to actually exercise
        # round over round, not just an idle-machine-every-round loop.
        budget = 20 * GB
        max_concurrent = 2
        queue = [
            candidate(job_id, priority=job_id * 100, models=(model(f"m{job_id}", 10 * GB),))
            for job_id in range(1, 6)
        ]
        running: list[SchedCandidate] = []
        admitted_history: list[int] = []

        rounds = 0
        while queue or running:
            rounds += 1
            assert rounds <= 20, "drain loop did not converge -- a job is stuck"

            admitted_ids = plan_admissions(
                queue, running, budget_bytes=budget, max_concurrent=max_concurrent
            )
            newly_admitted = [c for c in queue if c.job_id in admitted_ids]
            queue = [c for c in queue if c.job_id not in admitted_ids]
            running = running + newly_admitted
            admitted_history.extend(admitted_ids)

            # Invariant (i): resident memory never exceeds budget, in any
            # round where the running set isn't the deliberate
            # oversize/unknown-alone exception (rule 6).
            if running and not any(effectively_exclusive(job, budget) for job in running):
                assert resident_bytes(running) <= budget

            # Retire only the oldest (lowest-id) still-running job before
            # the next round -- everything else keeps running, so the next
            # round's `plan_admissions` call genuinely mixes leftover
            # running work with fresh candidates instead of starting idle
            # every time.
            if running:
                oldest = min(running, key=lambda c: c.job_id)
                running = [c for c in running if c.job_id != oldest.job_id]

        # Invariant (iv): every queued job is eventually admitted exactly
        # once -- nothing starves forever, nothing is admitted twice.
        assert sorted(admitted_history) == [1, 2, 3, 4, 5]
