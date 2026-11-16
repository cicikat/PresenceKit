"""Dream router composition and same-object legacy exports.

Endpoint signatures/scopes and registration order are preserved. Runtime helper
reads through this facade retain historical test/local-extension injection seams.
The Dream state machine and all persisted state remain in core.dream.
"""

import logging
import json
import re
import re as _re
import time
from pathlib import Path
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from admin.auth import require_scopes
from admin.routers._common import requested_char_id, resolve_requested_char_id
from core.config_loader import get_config
from core.sandbox import get_paths
from core.dream.dream_hud import derive_hud_v0 as _compute_hud_v0, _BOUNDARY_FACTOR
from core.dream.scenario_authoring import (
    _normalize_field_outline_scenario_yaml, _canonical_scenario_yaml,
    _OUTLINE_STAGE_FIELDS, _OUTLINE_BLOCK_FIELDS, _OUTLINE_LIST_FIELDS,
)

logger = logging.getLogger(__name__)
router = APIRouter()

from admin.routers import dream_common
from admin.routers.dream_common import (
    _SAFE_PRESET_RE,
    _SAFE_DREAM_ID_RE,
    _owner_uid,
    _active_dream_char_id,
    _validated_live_char_id,
    _validated_archive_char_id,
    _validated_dream_id,
    _authored_source,
    _log_dream_write,
)

from admin.routers import dream_sessions
from admin.routers.dream_sessions import (
    _requested_dream_id,
    _set_wake_observation,
    dream_enter,
    dream_chat,
    dream_exit,
    dream_wake,
    dream_resume,
)

from admin.routers import dream_archives
from admin.routers.dream_archives import (
    _read_archive_file,
    _safe_summary,
    _archive_metadata,
    _archive_path,
    _project_archive_message,
    dream_archive_list,
    dream_archive_detail,
)

from admin.routers import dream_observability
from admin.routers.dream_observability import (
    dream_invariants_get,
    dream_state_get,
    dream_operations_get,
    dream_stats_get,
)

from admin.routers import dream_presets
from admin.routers.dream_presets import (
    _VALID_MEMORY_ACCESS,
    _VALID_BOUNDARY_LEVEL,
    _VALID_WORLD_LAYER_BUILTIN,
    _VALID_LUCID_MODE,
    _VALID_DREAM_MODE,
    _VALID_SCENARIO_INJECTION_MODE,
    _ENUM_VALIDATORS,
    _valid_world_layer_values,
    _PATCH_ALLOWED,
    list_dream_presets,
    _preset_asset_path,
    _preset_write_path,
    _reload_dream_preset_registry,
    get_standalone_dream_preset,
    create_standalone_dream_preset,
    put_standalone_dream_preset,
    delete_standalone_dream_preset,
    dream_settings_get,
    dream_settings_patch,
)

from admin.routers import dream_worlds
from admin.routers.dream_worlds import (
    _SAFE_WORLD_RE,
    _RESERVED_WORLD_NAMES,
    _validate_world_name,
    _world_dir,
    _world_write_dir,
    _materialize_world_for_write,
    _preset_path,
    _dream_active_referencing_world,
    _reset_world_layer_setting_if,
    _ensure_default_world_template_seeded,
    list_dream_worlds,
    create_dream_world,
    rename_dream_world,
    delete_dream_world,
    get_dream_lorebook,
    _write_dream_lorebook,
    add_dream_lore_entry,
    update_dream_lore_entry,
    delete_dream_lore_entry,
    get_dream_preset,
    put_dream_preset,
)

from admin.routers import dream_scenarios
from admin.routers.dream_scenarios import (
    _SAFE_SCRIPT_ID_RE,
    _validate_scenario_id,
    _scenario_write_path,
    _scenario_read_path,
    _scenario_active,
    _parse_and_validate_scenario_yaml,
    _scenario_document_and_yaml,
    list_dream_scenarios,
    validate_dream_scenario,
    get_dream_scenario,
    create_dream_scenario,
    update_dream_scenario,
    delete_dream_scenario,
)

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
