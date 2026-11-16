"""Dream router composition and same-object legacy exports.

Endpoint signatures/scopes and registration order are preserved. All dependencies
point outward to the domain routers; no child locates helpers through this facade.
The Dream state machine and all persisted state remain in core.dream.
"""

from fastapi import APIRouter

router = APIRouter()

from admin.routers import dream_common

from admin.routers import dream_sessions
from admin.routers.dream_sessions import (dream_enter, dream_chat, dream_exit, dream_wake, dream_resume)

from admin.routers import dream_archives
from admin.routers.dream_archives import (dream_archive_list, dream_archive_detail)

from admin.routers import dream_observability
from admin.routers.dream_observability import (dream_invariants_get, dream_state_get, dream_operations_get, dream_stats_get)

from admin.routers import dream_presets
from admin.routers.dream_presets import (_valid_world_layer_values, list_dream_presets, get_standalone_dream_preset, create_standalone_dream_preset, put_standalone_dream_preset, delete_standalone_dream_preset, dream_settings_get, dream_settings_patch)

from admin.routers import dream_worlds
from admin.routers.dream_worlds import (list_dream_worlds, create_dream_world, rename_dream_world, delete_dream_world, get_dream_lorebook, add_dream_lore_entry, update_dream_lore_entry, delete_dream_lore_entry, get_dream_preset, put_dream_preset)

from admin.routers import dream_scenarios
from admin.routers.dream_scenarios import (list_dream_scenarios, validate_dream_scenario, get_dream_scenario, create_dream_scenario, update_dream_scenario, delete_dream_scenario)

for _module in (dream_sessions, dream_archives, dream_observability, dream_presets, dream_worlds, dream_scenarios):
    router.include_router(_module.router)

# Preserve the original interleaving (invariants/archive/sessions/state/presets/stats/settings).
_ROUTE_ORDER = (
    'dream_invariants_get',
    'dream_archive_list',
    'dream_archive_detail',
    'dream_enter',
    'dream_chat',
    'dream_exit',
    'dream_wake',
    'dream_resume',
    'dream_state_get',
    'dream_operations_get',
    'list_dream_presets',
    'get_standalone_dream_preset',
    'create_standalone_dream_preset',
    'put_standalone_dream_preset',
    'delete_standalone_dream_preset',
    'dream_stats_get',
    'dream_settings_get',
    'dream_settings_patch',
    'list_dream_worlds',
    'create_dream_world',
    'rename_dream_world',
    'delete_dream_world',
    'get_dream_lorebook',
    'add_dream_lore_entry',
    'update_dream_lore_entry',
    'delete_dream_lore_entry',
    'get_dream_preset',
    'put_dream_preset',
    'list_dream_scenarios',
    'validate_dream_scenario',
    'get_dream_scenario',
    'create_dream_scenario',
    'update_dream_scenario',
    'delete_dream_scenario',
)
router.routes.sort(key=lambda route: _ROUTE_ORDER.index(route.name))
