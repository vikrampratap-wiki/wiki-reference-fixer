"""
Pytest discovery entrypoint for all 4 Tiers of E2E tests.
Allows running `pytest e2e_tests/` without any custom configuration flags.
"""

# Re-export Tier 1 tests
from e2e_tests.tier1_feature_coverage import *  # noqa: F401, F403

# Re-export Tier 2 tests
from e2e_tests.tier2_boundary_corner import *  # noqa: F401, F403

# Re-export Tier 3 tests
from e2e_tests.tier3_pairwise import *  # noqa: F401, F403

# Re-export Tier 4 tests
from e2e_tests.tier4_real_world import *  # noqa: F401, F403
