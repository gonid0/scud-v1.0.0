import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://scud:password@localhost:5432/scud"
    session_lifetime_hours: int = 24
    refresh_lifetime_days: int = 30
    bloom_fp_rate: float = 0.001
    whitelist_hard_cap: int = 256
    # §3.4: hard cap on the bloom size so a per-reader filter always fits the
    # reader's deliverable filter size (transport budget). Above this the FP rate
    # rises gracefully (the whitelist absorbs the few active-key false positives).
    # Keep below the firmware MAX_FILTER_BYTES minus header/whitelist/sig overhead.
    filter_max_bloom_bytes: int = 100_000
    # --- bloom seed-scheme tuning (filter generation, see domain/filters.py) ---
    # E[whitelist] = active_count * fp_rate is governed by m_bits/k/n, NOT by the
    # hash seed. So we GROW m_bits (the real lever) until the closed-form
    # E[whitelist] drops to this fraction of the whitelist cap; the seed search
    # then only has to absorb sqrt(E)-scale variance, never bridge a gap.
    bloom_grow_margin: float = 0.5
    # If E[whitelist] STILL exceeds this multiple of the cap after growing m_bits to
    # the byte budget, no seed can fit — fail fast (ReaderOversaturated) instead of
    # building doomed blooms. ~1.5*cap is already several sigma past the band where
    # re-seeding can rescue a realization, so beyond it the loop is pure wasted CPU.
    bloom_saturation_skip_factor: float = 1.5
    # Seed search budget: normal when E[whitelist] is comfortably under the cap...
    seed_search_max_iterations: int = 100
    # ...extended in the marginal band (grow_margin*cap < E[whitelist] <=
    # skip_factor*cap), where extra rerolls can still land a realization under cap.
    seed_search_extended_iterations: int = 1000
    filter_generation_debounce_seconds: int = 5
    worker_poll_interval_seconds: int = 2
    # Максимум попыток подобрать serial так, чтобы key_id не давал FP в
    # текущем bloom-фильтре ридера. Если все попытки в FP — выпускаем ключ
    # и триггерим generate_filter (ридер добавит в whitelist при доставке).
    issue_key_bloom_retry_max: int = 5
    log_level: str = "INFO"
    # BE-SEC-04: deployment environment. Read from SCUD_ENV env var (not the
    # standard pydantic-settings field because other vars have no SCUD_ prefix).
    # Defaults to "dev". Set SCUD_ENV=production in prod deployments.
    environment: str = os.getenv("SCUD_ENV", "dev")

    model_config = {"env_file": ".env"}

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in ("production", "prod")


settings = Settings()
