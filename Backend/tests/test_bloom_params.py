"""Unit tests for select_bloom_params — the pure bloom sizing / seed selection.

Covers the branches the old fixed-m_bits / 100-seed loop never exercised:
adaptive m_bits growth, fail-fast oversaturation, and the extended-then-raise
seed path. Pure function, no DB.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scud.domain.filters import (
    ReaderOversaturated,
    _expected_whitelist,
    _k_for,
    _size_for_fp,
    select_bloom_params,
)

_FAR = 9_999_999_999  # expires_at well in the future


def _cfg(**over) -> SimpleNamespace:
    base = dict(
        bloom_fp_rate=0.001,
        whitelist_hard_cap=256,
        filter_max_bloom_bytes=100_000,
        bloom_grow_margin=0.5,
        bloom_saturation_skip_factor=1.5,
        seed_search_max_iterations=100,
        seed_search_extended_iterations=1000,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _keys(prefix: int, count: int) -> list[bytes]:
    return [bytes([prefix]) + i.to_bytes(15, "little") for i in range(count)]


def test_empty_revocations_returns_trivial_filter():
    sel = select_bloom_params([], [], _cfg())
    assert sel.m_bits == 64
    assert sel.k_hashes == 1
    assert sel.hash_seed == 0
    assert sel.bloom_bytes == bytes(8)
    assert sel.whitelist == []


def test_normal_seed_zero_no_growth():
    cfg = _cfg()
    n = 50
    candidates = _keys(0x10, n)
    active = [(k, _FAR) for k in _keys(0xAA, 5)]

    sel = select_bloom_params(candidates, active, cfg)

    expected_m = _size_for_fp(n, cfg.bloom_fp_rate, cfg.filter_max_bloom_bytes * 8)
    assert sel.m_bits == expected_m          # default size, no growth
    assert sel.hash_seed == 0                # hot path wins on the first seed
    assert len(sel.whitelist) <= cfg.whitelist_hard_cap


def test_growth_enlarges_m_bits():
    # High fp_rate makes the default filter small (q≈0.1), so a modest active
    # population pushes E[whitelist] over grow_margin*cap with room to grow.
    cfg = _cfg(bloom_fp_rate=0.1, whitelist_hard_cap=10)
    n = 100
    candidates = _keys(0x20, n)
    active = [(k, _FAR) for k in _keys(0xBB, 100)]

    default_m = _size_for_fp(n, cfg.bloom_fp_rate, cfg.filter_max_bloom_bytes * 8)
    sel = select_bloom_params(candidates, active, cfg)

    assert sel.m_bits > default_m                                  # grew the filter
    assert sel.expected_whitelist <= cfg.bloom_grow_margin * cfg.whitelist_hard_cap
    assert len(sel.whitelist) <= cfg.whitelist_hard_cap            # and it fits


def test_oversaturated_fails_fast():
    # Tiny byte budget => m_bits clamps to 64 bits, q≈0.54; 20 active gives
    # E[whitelist]≈11 > skip_factor*cap (1.5*4=6) => raise BEFORE any build.
    cfg = _cfg(filter_max_bloom_bytes=8, whitelist_hard_cap=4)
    candidates = _keys(0x30, 50)
    active = [(k, _FAR) for k in _keys(0xCC, 20)]

    with pytest.raises(ReaderOversaturated) as ei:
        select_bloom_params(candidates, active, cfg)

    assert ei.value.best_whitelist is None    # never entered the seed loop
    assert ei.value.expected_whitelist > cfg.bloom_saturation_skip_factor * cfg.whitelist_hard_cap


def test_extended_search_then_raises():
    # 8-bit bloom (q≈1): in the marginal band so the extended budget is used, but
    # every seed yields > cap collisions, so it exhausts the budget and raises with
    # best_whitelist set (proving it went through the seed loop, not fail-fast).
    cfg = _cfg(
        filter_max_bloom_bytes=1,
        whitelist_hard_cap=20,
        seed_search_extended_iterations=5,
    )
    candidates = _keys(0x40, 50)
    active = [(k, _FAR) for k in _keys(0x01, 25)]

    with pytest.raises(ReaderOversaturated) as ei:
        select_bloom_params(candidates, active, cfg)

    assert ei.value.best_whitelist is not None             # entered the seed loop
    assert ei.value.best_whitelist > cfg.whitelist_hard_cap


def test_expected_whitelist_decreases_with_m():
    # The closed form the growth search relies on: bigger filter => lower mean.
    n, a = 1000, 1000
    e_small = _expected_whitelist(8_000, _k_for(8_000, n), n, a)
    e_big = _expected_whitelist(80_000, _k_for(80_000, n), n, a)
    assert e_big < e_small


def test_seed_steps_by_k_disjoint_windows():
    # When seed 0 fits (normal case) the returned seed is 0; the schedule is t*k so
    # any selected seed is a multiple of k_hashes — disjoint murmur windows.
    cfg = _cfg()
    candidates = _keys(0x50, 30)
    active = [(k, _FAR) for k in _keys(0xDD, 3)]
    sel = select_bloom_params(candidates, active, cfg)
    assert sel.hash_seed % sel.k_hashes == 0
