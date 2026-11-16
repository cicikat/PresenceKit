# AR-02 dependency cleanup (327)

## Opening state

Base: `dc458737d06c60eb8ad9874169b9cb57a01e4e6a`. Current branch construction; unrelated dirty work is excluded. No production/config/client/deployment changes.

## Complete opening dependency ledger

Each row names the owning function and exact target. `import` rows include local imports; dynamic imports are listed separately. Calls through `_runtime` are private patch seams, not formal extension APIs. Callers: tool definitions are composed by registry and invoked by Execution/Invoker; policy/exposure serve Path A, Path C and tool discovery; Dream endpoints are mounted by dream.py; owner service serves desktop/mobile/owner-turn/companion. Tests patch these same facade attributes; migrated consumers and regressions are recorded per stage below.

| Source / function : line | Target | Kind |
|---|---|---|
| `core/tool_runtime/execution.py::_execute_structured_impl:37` | `core.tool_dispatcher` | import |
| `core/tool_runtime/execution.py::_execute_structured_impl:38` | `_EXECUTE_ALLOWED_ORIGINS` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:99` | `_QZONE_TOOL_NAMES` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:134` | `_TOOL_REGISTRY` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:159` | `_TOOL_REGISTRY` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:224` | `_schema_errors` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:231` | `_OWNER_ONLY_HARDWARE_TOOLS` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:44` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:47` | `_TOOL_REGISTRY` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:52` | `_time` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:87` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:137` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:140` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:161` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:166` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:171` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:172` | `_computer_use` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:176` | `_INTIFACE_TOOL_NAMES` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:179` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:196` | `_is_tool_enabled` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:199` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:206` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:228` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:355` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:39` | `logger` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:83` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:97` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:103` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:108` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:162` | `_device_file_tools` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:167` | `_gmail_tools` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:175` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:176` | `intiface_opted_in` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:183` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:222` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:240` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:246` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:303` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:304` | `_QZONE_TOOL_NAMES` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:307` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:308` | `logger` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:332` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:357` | `log_error` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:358` | `_log_execute_failure_context` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:362` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:373` | `log_error` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:374` | `_log_execute_failure_context` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:396` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_trace:57` | `_device_file_tools` | read/call |
| `core/tool_runtime/execution.py::_notify_status:120` | `inspect` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:151` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:152` | `logger` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:153` | `_execute_structured_impl` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:186` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:190` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:194` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:214` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:234` | `logger` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:262` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:297` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:300` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:359` | `_TOOL_FALLBACKS` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:372` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:375` | `_tool_result_pins` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:392` | `_execution_outcome` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:393` | `_TOOL_FALLBACKS` | read/call |
| `core/tool_runtime/execution.py::_trace:57` | `_gmail_tools` | read/call |
| `core/tool_runtime/execution.py::_trace:57` | `_intent_tools` | read/call |
| `core/tool_runtime/execution.py::_trace:78` | `logger` | read/call |
| `core/tool_runtime/execution.py::_notify_status:123` | `logger` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:169` | `_gmail_tools` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:174` | `_computer_use` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:256` | `logger` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:264` | `logger` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:290` | `_device_file_tools` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:369` | `logger` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:164` | `_device_file_tools` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:290` | `_gmail_tools` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:290` | `_intent_tools` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:326` | `logger` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:351` | `logger` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:384` | `_mcp_server_reported_error` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:90` | `get_config` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:232` | `get_config` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:321` | `_TOOL_REGISTRY` | read/call |
| `core/tool_runtime/execution.py::_trace:74` | `_TOOL_REGISTRY` | read/call |
| `core/tool_runtime/execution.py::_execute_structured_impl:106` | `_TOOL_REGISTRY` | read/call |
| `core/tool_runtime/execution.py::_trace:67` | `_time` | read/call |
| `core/tool_runtime/exposure.py::get_tools_schema:14` | `core.tool_dispatcher` | import |
| `core/tool_runtime/exposure.py::_has_open_object:79` | `core.tool_dispatcher` | import |
| `core/tool_runtime/exposure.py::format_mcp_opaque_params_note:100` | `core.tool_dispatcher` | import |
| `core/tool_runtime/exposure.py::_build_probe_prompt:153` | `core.tool_dispatcher` | import |
| `core/tool_runtime/exposure.py::get_probe_prompt:202` | `core.tool_dispatcher` | import |
| `core/tool_runtime/exposure.py::get_tool_loop_relay_prompt:222` | `core.tool_dispatcher` | import |
| `core/tool_runtime/exposure.py::get_tools_schema:15` | `get_active_char_name` | read/call |
| `core/tool_runtime/exposure.py::_build_probe_prompt:172` | `get_active_char_name` | read/call |
| `core/tool_runtime/exposure.py::get_probe_prompt:203` | `_build_probe_prompt` | read/call |
| `core/tool_runtime/exposure.py::get_tool_loop_relay_prompt:223` | `_build_probe_prompt` | read/call |
| `core/tool_runtime/exposure.py::get_tools_schema:18` | `_TOOL_REGISTRY` | read/call |
| `core/tool_runtime/exposure.py::get_tools_schema:29` | `_QZONE_TOOL_NAMES` | read/call |
| `core/tool_runtime/exposure.py::_has_open_object:86` | `_has_open_object` | read/call |
| `core/tool_runtime/exposure.py::_build_probe_prompt:173` | `_TOOL_REGISTRY` | read/call |
| `core/tool_runtime/exposure.py::get_tools_schema:44` | `_is_tool_enabled` | read/call |
| `core/tool_runtime/exposure.py::get_tools_schema:70` | `mcp_domain_selector_allows` | read/call |
| `core/tool_runtime/exposure.py::_has_open_object:88` | `_has_open_object` | read/call |
| `core/tool_runtime/exposure.py::format_mcp_opaque_params_note:105` | `_TOOL_REGISTRY` | read/call |
| `core/tool_runtime/exposure.py::_build_probe_prompt:176` | `_device_file_tools` | read/call |
| `core/tool_runtime/exposure.py::get_tools_schema:23` | `_computer_use` | read/call |
| `core/tool_runtime/exposure.py::get_tools_schema:25` | `_device_file_tools` | read/call |
| `core/tool_runtime/exposure.py::get_tools_schema:27` | `_gmail_tools` | read/call |
| `core/tool_runtime/exposure.py::format_mcp_opaque_params_note:106` | `_has_open_object` | read/call |
| `core/tool_runtime/exposure.py::_build_probe_prompt:178` | `_computer_use` | read/call |
| `core/tool_runtime/exposure.py::get_tools_schema:27` | `_gmail_tools` | read/call |
| `core/tool_runtime/exposure.py::get_tools_schema:71` | `_TOOL_REGISTRY` | read/call |
| `core/tool_runtime/exposure.py::format_mcp_opaque_params_note:124` | `_TOOL_REGISTRY` | read/call |
| `core/tool_runtime/exposure.py::get_tools_schema:23` | `_computer_use` | read/call |
| `core/tool_runtime/exposure.py::get_tools_schema:23` | `_computer_use` | read/call |
| `core/tool_runtime/exposure.py::get_tools_schema:25` | `_device_file_tools` | read/call |
| `core/tool_runtime/exposure.py::format_mcp_opaque_params_note:128` | `_MCP_DOC_DESCRIPTION_HINTS` | read/call |
| `core/tool_runtime/exposure.py::get_tools_schema:34` | `get_config` | read/call |
| `core/tool_runtime/outcome.py::_log_execute_failure_context:13` | `core.tool_dispatcher` | import |
| `core/tool_runtime/outcome.py::_mcp_server_reported_error:37` | `core.tool_dispatcher` | import |
| `core/tool_runtime/outcome.py::_execution_outcome:60` | `core.tool_dispatcher` | import |
| `core/tool_runtime/outcome.py::_execution_outcome:61` | `ToolExecutionOutcome` | read/call |
| `core/tool_runtime/outcome.py::_log_execute_failure_context:20` | `logger` | read/call |
| `core/tool_runtime/outcome.py::_mcp_server_reported_error:38` | `_MCP_SERVER_ERROR_PREFIX` | read/call |
| `core/tool_runtime/outcome.py::_log_execute_failure_context:14` | `_device_file_tools` | read/call |
| `core/tool_runtime/outcome.py::_log_execute_failure_context:17` | `json` | read/call |
| `core/tool_runtime/outcome.py::_log_execute_failure_context:14` | `_computer_use` | read/call |
| `core/tool_runtime/outcome.py::_log_execute_failure_context:14` | `_gmail_tools` | read/call |
| `core/tool_runtime/outcome.py::_mcp_server_reported_error:39` | `_MCP_SERVER_ERROR_PREFIX` | read/call |
| `core/tool_runtime/policy.py::_require_memory_read_scope:5` | `core.tool_dispatcher` | import |
| `core/tool_runtime/policy.py::get_tool_effect:14` | `core.tool_dispatcher` | import |
| `core/tool_runtime/policy.py::is_side_effect_tool:25` | `core.tool_dispatcher` | import |
| `core/tool_runtime/policy.py::tool_fast_path_risk:45` | `core.tool_dispatcher` | import |
| `core/tool_runtime/policy.py::_is_tool_enabled:53` | `core.tool_dispatcher` | import |
| `core/tool_runtime/policy.py::intiface_opted_in:121` | `core.tool_dispatcher` | import |
| `core/tool_runtime/policy.py::mcp_domain_selector_allows:128` | `core.tool_dispatcher` | import |
| `core/tool_runtime/policy.py::_effective_tool_loop_enabled:169` | `core.tool_dispatcher` | import |
| `core/tool_runtime/policy.py::tool_loop_active:194` | `core.tool_dispatcher` | import |
| `core/tool_runtime/policy.py::is_side_effect_tool:26` | `get_tool_effect` | read/call |
| `core/tool_runtime/policy.py::is_side_effect_tool:36` | `_SIDE_EFFECT_TOOLS` | read/call |
| `core/tool_runtime/policy.py::_is_tool_enabled:54` | `_QZONE_TOOL_NAMES` | read/call |
| `core/tool_runtime/policy.py::is_side_effect_tool:33` | `_TOOL_REGISTRY` | read/call |
| `core/tool_runtime/policy.py::tool_fast_path_risk:46` | `is_side_effect_tool` | read/call |
| `core/tool_runtime/policy.py::_is_tool_enabled:89` | `_INTIFACE_TOOL_NAMES` | read/call |
| `core/tool_runtime/policy.py::mcp_domain_selector_allows:132` | `get_config` | read/call |
| `core/tool_runtime/policy.py::tool_loop_active:196` | `_effective_tool_loop_enabled` | read/call |
| `core/tool_runtime/policy.py::_is_tool_enabled:64` | `_computer_use` | read/call |
| `core/tool_runtime/policy.py::_is_tool_enabled:89` | `intiface_opted_in` | read/call |
| `core/tool_runtime/policy.py::_is_tool_enabled:98` | `get_config` | read/call |
| `core/tool_runtime/policy.py::_is_tool_enabled:99` | `_is_tool_enabled` | read/call |
| `core/tool_runtime/policy.py::tool_loop_active:195` | `get_config` | read/call |
| `core/tool_runtime/policy.py::get_tool_effect:15` | `_TOOL_REGISTRY` | read/call |
| `core/tool_runtime/policy.py::_is_tool_enabled:64` | `_computer_use` | read/call |
| `core/tool_runtime/policy.py::_is_tool_enabled:72` | `get_config` | read/call |
| `core/tool_runtime/policy.py::intiface_opted_in:122` | `get_config` | read/call |
| `core/tool_runtime/policy.py::mcp_domain_selector_allows:148` | `logger` | read/call |
| `core/tool_runtime/policy.py::_is_tool_enabled:75` | `get_config` | read/call |
| `core/tool_runtime/policy.py::_is_tool_enabled:60` | `_TOOL_REGISTRY` | read/call |
| `core/tool_runtime/policy.py::tool_loop_active:198` | `get_config` | read/call |
| `core/tool_runtime/registry.py::build_registry:7` | `core.tool_dispatcher._register_artifact_tools` | import |
| `core/tool_runtime/registry.py::build_registry:9` | `core.tool_dispatcher._register_drinking_tools` | import |
| `core/tool_runtime/registry.py::build_registry:11` | `core.tool_dispatcher._register_self_tools` | import |
| `core/tool_runtime/registry.py::build_registry:13` | `core.tool_dispatcher._register_reminder_tools` | import |
| `core/tool_runtime/registry.py::build_registry:15` | `core.tool_dispatcher._register_qzone_tools` | import |
| `core/tool_runtime/registry.py::build_registry:17` | `core.tool_dispatcher._register_listening_tools` | import |
| `core/tool_runtime/registry.py::build_registry:19` | `core.tool_dispatcher._register_image_gen_tools` | import |
| `core/tool_runtime/registry.py::build_registry:21` | `core.tool_dispatcher._gmail_tools` | import |
| `core/tool_runtime/registry.py::build_registry:23` | `core.tool_dispatcher._device_file_tools` | import |
| `core/tool_runtime/registry.py::build_registry:25` | `core.tool_dispatcher._intent_tools` | import |
| `core/tool_runtime/validation.py::_schema_value_matches:4` | `core.tool_dispatcher` | import |
| `core/tool_runtime/validation.py::_schema_errors:20` | `core.tool_dispatcher` | import |
| `core/tool_runtime/validation.py::_schema_errors:24` | `_schema_value_matches` | read/call |
| `core/tool_runtime/validation.py::_schema_errors:49` | `_schema_errors` | read/call |
| `core/tool_runtime/validation.py::_schema_value_matches:6` | `_schema_value_matches` | read/call |
| `core/tool_runtime/validation.py::_schema_errors:44` | `_schema_errors` | read/call |
| `core/tool_runtime/validation.py::_schema_errors:30` | `_schema_errors` | read/call |
| `core/tools/devices_definitions.py::_device_shutdown:7` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_device_sleep:26` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::resolve_action_target:48` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_is_desktop_active:54` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_push_desktop_action:73` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_desktop_minimize_wrapper:118` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_desktop_open_url_wrapper:124` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_desktop_play_pause_wrapper:130` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_desktop_notify_wrapper:136` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_toy_invite_wrapper:145` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_dream_invite_wrapper:152` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_phone_control_start_wrapper:164` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_toy_vibrate_wrapper:191` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_toy_stop_wrapper:204` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_toy_pattern_wrapper:216` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_toy_job_status_wrapper:222` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_peek_screen_content_wrapper:228` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_computer_use_start:234` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_computer_use_status:244` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_computer_use_cancel:249` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_agent_session_start:254` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_agent_session_status:264` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_agent_session_control:270` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_observe_user_screen_wrapper:282` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_observe_video_call_camera_wrapper:288` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_invite_video_call_wrapper:296` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_invoke_observe_user_screen:308` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::_invoke_agent_session_start:328` | `core.tool_dispatcher` | import |
| `core/tools/devices_definitions.py::register_tools:346` | `core.tool_dispatcher._agent_session_id` | import |
| `core/tools/devices_definitions.py::resolve_action_target:49` | `_ACTION_TARGETS` | read/call |
| `core/tools/devices_definitions.py::_push_desktop_action:75` | `resolve_action_target` | read/call |
| `core/tools/devices_definitions.py::_push_desktop_action:99` | `_is_desktop_active` | read/call |
| `core/tools/devices_definitions.py::_desktop_minimize_wrapper:119` | `_push_desktop_action` | read/call |
| `core/tools/devices_definitions.py::_desktop_open_url_wrapper:125` | `_push_desktop_action` | read/call |
| `core/tools/devices_definitions.py::_desktop_play_pause_wrapper:131` | `_push_desktop_action` | read/call |
| `core/tools/devices_definitions.py::_desktop_notify_wrapper:138` | `get_active_char_name` | read/call |
| `core/tools/devices_definitions.py::_desktop_notify_wrapper:139` | `_push_desktop_action` | read/call |
| `core/tools/devices_definitions.py::_toy_invite_wrapper:146` | `_push_desktop_action` | read/call |
| `core/tools/devices_definitions.py::_dream_invite_wrapper:153` | `_push_desktop_action` | read/call |
| `core/tools/devices_definitions.py::_computer_use_start:239` | `_computer_use` | read/call |
| `core/tools/devices_definitions.py::_computer_use_start:240` | `_computer_use` | read/call |
| `core/tools/devices_definitions.py::_computer_use_status:245` | `_computer_use` | read/call |
| `core/tools/devices_definitions.py::_computer_use_cancel:250` | `_computer_use` | read/call |
| `core/tools/devices_definitions.py::_agent_session_start:259` | `_agent_sessions` | read/call |
| `core/tools/devices_definitions.py::_agent_session_start:260` | `json` | read/call |
| `core/tools/devices_definitions.py::_agent_session_status:265` | `json` | read/call |
| `core/tools/devices_definitions.py::_agent_session_control:274` | `_agent_sessions` | read/call |
| `core/tools/devices_definitions.py::_agent_session_control:278` | `json` | read/call |
| `core/tools/devices_definitions.py::_device_shutdown:12` | `platform` | read/call |
| `core/tools/devices_definitions.py::_device_shutdown:21` | `log_error` | read/call |
| `core/tools/devices_definitions.py::_device_sleep:31` | `platform` | read/call |
| `core/tools/devices_definitions.py::_device_sleep:42` | `log_error` | read/call |
| `core/tools/devices_definitions.py::_push_desktop_action:77` | `logger` | read/call |
| `core/tools/devices_definitions.py::_push_desktop_action:87` | `logger` | read/call |
| `core/tools/devices_definitions.py::_computer_use_status:245` | `_computer_use` | read/call |
| `core/tools/devices_definitions.py::_computer_use_cancel:250` | `_computer_use` | read/call |
| `core/tools/devices_definitions.py::_agent_session_start:259` | `_TaskPrincipal` | read/call |
| `core/tools/devices_definitions.py::_agent_session_status:265` | `_agent_sessions` | read/call |
| `core/tools/devices_definitions.py::_agent_session_control:275` | `_TaskPrincipal` | read/call |
| `core/tools/devices_definitions.py::_device_shutdown:14` | `subprocess` | read/call |
| `core/tools/devices_definitions.py::_device_sleep:33` | `subprocess` | read/call |
| `core/tools/devices_definitions.py::_push_desktop_action:85` | `logger` | read/call |
| `core/tools/devices_definitions.py::_push_desktop_action:91` | `logger` | read/call |
| `core/tools/devices_definitions.py::_push_desktop_action:107` | `_json` | read/call |
| `core/tools/devices_definitions.py::_push_desktop_action:110` | `_json` | read/call |
| `core/tools/devices_definitions.py::_agent_session_status:265` | `_TaskPrincipal` | read/call |
| `core/tools/devices_definitions.py::_device_shutdown:16` | `subprocess` | read/call |
| `core/tools/devices_definitions.py::_device_sleep:35` | `subprocess` | read/call |
| `core/tools/devices_definitions.py::_device_sleep:37` | `subprocess` | read/call |
| `core/tools/devices_definitions.py::_invite_video_call_wrapper:300` | `get_config` | read/call |
| `core/tools/files_agents_definitions.py::_fs_list_wrapper:13` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_fs_read_wrapper:25` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_workspace_list_wrapper:31` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_workspace_read_wrapper:38` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_workspace_tool_request:46` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_workspace_write_wrapper:52` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_workspace_create_wrapper:86` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_workspace_update_wrapper:91` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_workspace_delete_wrapper:96` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_workspace_undo_wrapper:129` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_process_run_wrapper:172` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_start_agent_task_wrapper:209` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_get_agent_task_wrapper:251` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_cancel_agent_task_wrapper:266` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_browser_automation_wrapper:290` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_manage_self_capability_wrapper:319` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_list_self_capabilities_wrapper:352` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_read_self_action_history_wrapper:378` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_invoke_device_workspace_list:392` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_invoke_gmail_list_messages:410` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_invoke_list_character_intents:428` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_invoke_self_tool_define:446` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_invoke_fs_list:473` | `core.tool_dispatcher` | import |
| `core/tools/files_agents_definitions.py::_workspace_list_wrapper:34` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_workspace_create_wrapper:87` | `_workspace_write_wrapper` | read/call |
| `core/tools/files_agents_definitions.py::_workspace_update_wrapper:92` | `_workspace_write_wrapper` | read/call |
| `core/tools/files_agents_definitions.py::_process_run_wrapper:187` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_manage_self_capability_wrapper:342` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_list_self_capabilities_wrapper:371` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_read_self_action_history_wrapper:388` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_invoke_device_workspace_list:404` | `_device_file_tools` | read/call |
| `core/tools/files_agents_definitions.py::_workspace_write_wrapper:66` | `_workspace_tool_request` | read/call |
| `core/tools/files_agents_definitions.py::_workspace_write_wrapper:70` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_workspace_write_wrapper:73` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_workspace_write_wrapper:78` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_workspace_delete_wrapper:109` | `_workspace_tool_request` | read/call |
| `core/tools/files_agents_definitions.py::_workspace_delete_wrapper:113` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_workspace_delete_wrapper:116` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_workspace_delete_wrapper:121` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_workspace_undo_wrapper:142` | `_workspace_tool_request` | read/call |
| `core/tools/files_agents_definitions.py::_workspace_undo_wrapper:146` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_workspace_undo_wrapper:149` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_workspace_undo_wrapper:154` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_start_agent_task_wrapper:233` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_get_agent_task_wrapper:257` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_cancel_agent_task_wrapper:272` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_browser_automation_wrapper:301` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_invoke_gmail_list_messages:422` | `_gmail_tools` | read/call |
| `core/tools/files_agents_definitions.py::_invoke_list_character_intents:440` | `_intent_tools` | read/call |
| `core/tools/files_agents_definitions.py::_workspace_write_wrapper:58` | `hashlib` | read/call |
| `core/tools/files_agents_definitions.py::_workspace_delete_wrapper:101` | `hashlib` | read/call |
| `core/tools/files_agents_definitions.py::_workspace_undo_wrapper:134` | `hashlib` | read/call |
| `core/tools/files_agents_definitions.py::_start_agent_task_wrapper:213` | `hashlib` | read/call |
| `core/tools/files_agents_definitions.py::_start_agent_task_wrapper:245` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_get_agent_task_wrapper:260` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_cancel_agent_task_wrapper:275` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_browser_automation_wrapper:294` | `hashlib` | read/call |
| `core/tools/files_agents_definitions.py::_browser_automation_wrapper:303` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_start_agent_task_wrapper:240` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_list_self_capabilities_wrapper:370` | `uuid` | read/call |
| `core/tools/files_agents_definitions.py::_read_self_action_history_wrapper:385` | `uuid` | read/call |
| `core/tools/files_agents_definitions.py::_process_run_wrapper:182` | `hashlib` | read/call |
| `core/tools/files_agents_definitions.py::_start_agent_task_wrapper:214` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_browser_automation_wrapper:294` | `json` | read/call |
| `core/tools/files_agents_definitions.py::_process_run_wrapper:183` | `json` | read/call |
| `core/tools/information_definitions.py::_get_current_time:7` | `core.tool_dispatcher` | import |
| `core/tools/information_definitions.py::_weather_wrapper:15` | `core.tool_dispatcher` | import |
| `core/tools/information_definitions.py::_web_search_wrapper:21` | `core.tool_dispatcher` | import |
| `core/tools/information_definitions.py::_read_xiaohongshu_wrapper:27` | `core.tool_dispatcher` | import |
| `core/tools/information_definitions.py::_invoke_read_xiaohongshu:33` | `core.tool_dispatcher` | import |
| `core/tools/information_definitions.py::_invoke_web_search:54` | `core.tool_dispatcher` | import |
| `core/tools/information_definitions.py::register_tools:80` | `core.tool_dispatcher._xhs_continuation` | import |
| `core/tools/information_definitions.py::_invoke_read_xiaohongshu:47` | `_require_memory_read_scope` | read/call |
| `core/tools/life_media_definitions.py::_play_song_wrapper:8` | `core.tool_dispatcher` | import |
| `core/tools/life_media_definitions.py::_read_toy_file_wrapper:51` | `core.tool_dispatcher` | import |
| `core/tools/life_media_definitions.py::_write_toy_file_wrapper:64` | `core.tool_dispatcher` | import |
| `core/tools/life_media_definitions.py::_reread_image_wrapper:70` | `core.tool_dispatcher` | import |
| `core/tools/life_media_definitions.py::_life_records_wrapper:84` | `core.tool_dispatcher` | import |
| `core/tools/life_media_definitions.py::_invoke_read_toy_file:121` | `core.tool_dispatcher` | import |
| `core/tools/life_media_definitions.py::register_tools:139` | `core.tool_dispatcher._get_ambient_state` | import |
| `core/tools/life_media_definitions.py::register_tools:139` | `core.tool_dispatcher.water_garden` | import |
| `core/tools/life_media_definitions.py::_play_song_wrapper:41` | `_push_desktop_action` | read/call |
| `core/tools/life_media_definitions.py::_life_records_wrapper:90` | `get_config` | read/call |
| `core/tools/memory_definitions.py::_read_diary_wrapper:7` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_backfill_diary_wrapper:13` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_read_watch_wrapper:19` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_read_hds_heart_rate_wrapper:25` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_search_diary_wrapper:33` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_search_documents_wrapper:39` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_read_document_wrapper:45` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_search_character_notes_wrapper:51` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_read_document_notes_wrapper:57` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_write_document_note_wrapper:63` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_get_profile_wrapper:70` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_get_episodic_wrapper:88` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_read_long_user_message_wrapper:97` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_revise_memory_wrapper:103` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_forget_episodic_wrapper:118` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_clear_midterm_wrapper:138` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_revise_user_profile_wrapper:150` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_exit_yandere_wrapper:161` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_invoke_read_document_notes:179` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_invoke_read_life_records:201` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_invoke_get_profile:222` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_invoke_update_memory_dossier:241` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_invoke_backfill_diary:260` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_invoke_read_diary:279` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_invoke_get_listening_state:303` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_invoke_pin_character_image_reference:326` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_invoke_read_hds_heart_rate:345` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_invoke_read_watch:358` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::_invoke_revise_memory:370` | `core.tool_dispatcher` | import |
| `core/tools/memory_definitions.py::register_tools:382` | `core.tool_dispatcher._get_memory_consolidation_status_wrapper` | import |
| `core/tools/memory_definitions.py::register_tools:382` | `core.tool_dispatcher._quote_notebook` | import |
| `core/tools/memory_definitions.py::register_tools:382` | `core.tool_dispatcher._read_food_preferences` | import |
| `core/tools/memory_definitions.py::register_tools:382` | `core.tool_dispatcher._read_memory_dossier_wrapper` | import |
| `core/tools/memory_definitions.py::register_tools:382` | `core.tool_dispatcher._request_memory_consolidation_wrapper` | import |
| `core/tools/memory_definitions.py::register_tools:382` | `core.tool_dispatcher._search_dossier_events_wrapper` | import |
| `core/tools/memory_definitions.py::register_tools:382` | `core.tool_dispatcher._search_memory_dossiers_wrapper` | import |
| `core/tools/memory_definitions.py::register_tools:382` | `core.tool_dispatcher._tool_result_pins` | import |
| `core/tools/memory_definitions.py::register_tools:382` | `core.tool_dispatcher._update_memory_dossier_wrapper` | import |
| `core/tools/memory_definitions.py::_invoke_read_document_notes:194` | `_require_memory_read_scope` | read/call |
| `core/tools/memory_definitions.py::_invoke_read_life_records:215` | `_require_memory_read_scope` | read/call |
| `core/tools/memory_definitions.py::_invoke_get_profile:234` | `_require_memory_read_scope` | read/call |
| `core/tools/memory_definitions.py::_invoke_update_memory_dossier:253` | `_require_memory_read_scope` | read/call |
| `core/tools/memory_definitions.py::_invoke_backfill_diary:272` | `_require_memory_read_scope` | read/call |
| `core/tools/memory_definitions.py::_invoke_read_diary:291` | `_require_memory_read_scope` | read/call |
| `core/tools/memory_definitions.py::_invoke_read_hds_heart_rate:351` | `_require_memory_read_scope` | read/call |
| `core/tools/memory_definitions.py::_read_hds_heart_rate_wrapper:27` | `json` | read/call |
| `core/tools/memory_definitions.py::_invoke_read_diary:292` | `_read_diary_wrapper` | read/call |
| `core/tools/memory_definitions.py::_invoke_read_diary:292` | `_search_diary_wrapper` | read/call |
| `core/tools/memory_definitions.py::register_tools:406` | `__import__('core.tools.activity_records', fromlist=['query_activity_records'])` | dynamic |
| `core/tools/memory_definitions.py::register_tools:422` | `__import__('core.tools.message_context', fromlist=['read_message_context'])` | dynamic |
| `core/tools/memory_definitions.py::register_tools:753` | `__import__('core.tools.event_tools', fromlist=['search_events_wrapper'])` | dynamic |
| `core/tools/memory_definitions.py::register_tools:773` | `__import__('core.tools.event_tools', fromlist=['expand_event_window_wrapper'])` | dynamic |
| `core/tools/memory_definitions.py::register_tools:788` | `__import__('core.tools.event_tools', fromlist=['get_related_events_wrapper'])` | dynamic |
| `core/tools/memory_definitions.py::_exit_yandere_wrapper:170` | `logger` | read/call |
| `core/tools/memory_definitions.py::_exit_yandere_wrapper:175` | `get_active_char_name` | read/call |
| `core/tools/memory_definitions.py::_read_diary_wrapper:9` | `_active_char_id` | read/call |
| `core/tools/memory_definitions.py::_read_watch_wrapper:21` | `_active_char_id` | read/call |
| `core/tools/memory_definitions.py::_search_diary_wrapper:35` | `_active_char_id` | read/call |
| `core/tools/memory_definitions.py::_get_episodic_wrapper:92` | `get_char_name` | read/call |
| `core/tools/memory_definitions.py::_read_hds_heart_rate_wrapper:27` | `_active_char_id` | read/call |
| `core/tools/memory_definitions.py::_invoke_read_document_notes:191` | `get_config` | read/call |
| `core/owner_chat/prepare.py::_probe_and_execute_tools:125` | `core.tool_dispatcher` | import |
| `core/owner_chat/service.py::run_owner_chat_turn:132` | `core.tool_dispatcher` | import |
| `core/owner_chat/service.py::_execute_owner_chat_turn_locked:208` | `admin.routers.chat._probe_and_execute_tools` | import |
| `admin/routers/dream_archives.py::_read_archive_file:20` | `admin.routers.dream` | import |
| `admin/routers/dream_archives.py::_safe_summary:42` | `admin.routers.dream` | import |
| `admin/routers/dream_archives.py::_archive_metadata:53` | `admin.routers.dream` | import |
| `admin/routers/dream_archives.py::_archive_path:97` | `admin.routers.dream` | import |
| `admin/routers/dream_archives.py::_project_archive_message:108` | `admin.routers.dream` | import |
| `admin/routers/dream_archives.py::dream_archive_list:139` | `admin.routers.dream` | import |
| `admin/routers/dream_archives.py::dream_archive_detail:180` | `admin.routers.dream` | import |
| `admin/routers/dream_archives.py::_archive_metadata:54` | `_read_archive_file` | read/call |
| `admin/routers/dream_archives.py::_archive_metadata:69` | `_safe_summary` | read/call |
| `admin/routers/dream_archives.py::dream_archive_list:140` | `_validated_archive_char_id` | read/call |
| `admin/routers/dream_archives.py::dream_archive_detail:181` | `_validated_dream_id` | read/call |
| `admin/routers/dream_archives.py::dream_archive_detail:182` | `_validated_archive_char_id` | read/call |
| `admin/routers/dream_archives.py::dream_archive_detail:183` | `_archive_path` | read/call |
| `admin/routers/dream_archives.py::dream_archive_detail:193` | `_read_archive_file` | read/call |
| `admin/routers/dream_archives.py::dream_archive_list:148` | `_archive_metadata` | read/call |
| `admin/routers/dream_archives.py::dream_archive_detail:192` | `HTTPException` | read/call |
| `admin/routers/dream_archives.py::dream_archive_detail:195` | `HTTPException` | read/call |
| `admin/routers/dream_archives.py::dream_archive_detail:211` | `_archive_metadata` | read/call |
| `admin/routers/dream_archives.py::_safe_summary:46` | `json` | read/call |
| `admin/routers/dream_archives.py::dream_archive_list:141` | `get_paths` | read/call |
| `admin/routers/dream_archives.py::dream_archive_list:152` | `_owner_uid` | read/call |
| `admin/routers/dream_archives.py::dream_archive_detail:207` | `_project_archive_message` | read/call |
| `admin/routers/dream_archives.py::_archive_path:98` | `get_paths` | read/call |
| `admin/routers/dream_archives.py::dream_archive_list:146` | `_SAFE_DREAM_ID_RE` | read/call |
| `admin/routers/dream_archives.py::dream_archive_list:152` | `get_paths` | read/call |
| `admin/routers/dream_archives.py::dream_archive_list:161` | `logger` | read/call |
| `admin/routers/dream_archives.py::dream_archive_detail:187` | `_owner_uid` | read/call |
| `admin/routers/dream_archives.py::_read_archive_file:28` | `json` | read/call |
| `admin/routers/dream_archives.py::_archive_metadata:69` | `get_paths` | read/call |
| `admin/routers/dream_archives.py::dream_archive_list:158` | `_owner_uid` | read/call |
| `admin/routers/dream_archives.py::dream_archive_detail:191` | `logger` | read/call |
| `admin/routers/dream_archives.py::dream_archive_list:156` | `_SAFE_DREAM_ID_RE` | read/call |
| `admin/routers/dream_common.py::_owner_uid:26` | `admin.routers.dream` | import |
| `admin/routers/dream_common.py::_active_dream_char_id:34` | `admin.routers.dream` | import |
| `admin/routers/dream_common.py::_validated_live_char_id:46` | `admin.routers.dream` | import |
| `admin/routers/dream_common.py::_validated_archive_char_id:55` | `admin.routers.dream` | import |
| `admin/routers/dream_common.py::_validated_dream_id:68` | `admin.routers.dream` | import |
| `admin/routers/dream_common.py::_authored_source:76` | `admin.routers.dream` | import |
| `admin/routers/dream_common.py::_log_dream_write:83` | `admin.routers.dream` | import |
| `admin/routers/dream_common.py::_validated_live_char_id:47` | `resolve_requested_char_id` | read/call |
| `admin/routers/dream_common.py::_validated_archive_char_id:56` | `requested_char_id` | read/call |
| `admin/routers/dream_common.py::_validated_archive_char_id:61` | `_active_dream_char_id` | read/call |
| `admin/routers/dream_common.py::_owner_uid:29` | `HTTPException` | read/call |
| `admin/routers/dream_common.py::_active_dream_char_id:40` | `resolve_requested_char_id` | read/call |
| `admin/routers/dream_common.py::_validated_live_char_id:47` | `_active_dream_char_id` | read/call |
| `admin/routers/dream_common.py::_validated_live_char_id:49` | `HTTPException` | read/call |
| `admin/routers/dream_common.py::_validated_archive_char_id:63` | `HTTPException` | read/call |
| `admin/routers/dream_common.py::_validated_dream_id:71` | `HTTPException` | read/call |
| `admin/routers/dream_common.py::_log_dream_write:84` | `logger` | read/call |
| `admin/routers/dream_common.py::_validated_live_char_id:48` | `_SAFE_DREAM_ID_RE` | read/call |
| `admin/routers/dream_common.py::_validated_archive_char_id:59` | `HTTPException` | read/call |
| `admin/routers/dream_common.py::_validated_archive_char_id:62` | `_SAFE_DREAM_ID_RE` | read/call |
| `admin/routers/dream_common.py::_validated_dream_id:70` | `_SAFE_DREAM_ID_RE` | read/call |
| `admin/routers/dream_common.py::_log_dream_write:87` | `_authored_source` | read/call |
| `admin/routers/dream_common.py::_validated_archive_char_id:58` | `_SAFE_DREAM_ID_RE` | read/call |
| `admin/routers/dream_common.py::_owner_uid:27` | `get_config` | read/call |
| `admin/routers/dream_observability.py::dream_invariants_get:24` | `admin.routers.dream` | import |
| `admin/routers/dream_observability.py::dream_state_get:51` | `admin.routers.dream` | import |
| `admin/routers/dream_observability.py::dream_operations_get:157` | `admin.routers.dream` | import |
| `admin/routers/dream_observability.py::dream_stats_get:311` | `admin.routers.dream` | import |
| `admin/routers/dream_observability.py::dream_invariants_get:26` | `_validated_live_char_id` | read/call |
| `admin/routers/dream_observability.py::dream_state_get:52` | `_owner_uid` | read/call |
| `admin/routers/dream_observability.py::dream_state_get:53` | `_validated_live_char_id` | read/call |
| `admin/routers/dream_observability.py::dream_operations_get:158` | `_owner_uid` | read/call |
| `admin/routers/dream_observability.py::dream_operations_get:164` | `_active_dream_char_id` | read/call |
| `admin/routers/dream_observability.py::dream_stats_get:313` | `_validated_live_char_id` | read/call |
| `admin/routers/dream_observability.py::dream_invariants_get:27` | `_owner_uid` | read/call |
| `admin/routers/dream_observability.py::dream_state_get:71` | `_VALID_SCENARIO_INJECTION_MODE` | read/call |
| `admin/routers/dream_observability.py::dream_operations_get:208` | `dream_archive_list` | read/call |
| `admin/routers/dream_observability.py::dream_operations_get:261` | `_SAFE_DREAM_ID_RE` | read/call |
| `admin/routers/dream_observability.py::dream_operations_get:233` | `json` | read/call |
| `admin/routers/dream_observability.py::dream_operations_get:264` | `json` | read/call |
| `admin/routers/dream_presets.py::_valid_world_layer_values:62` | `admin.routers.dream` | import |
| `admin/routers/dream_presets.py::list_dream_presets:72` | `admin.routers.dream` | import |
| `admin/routers/dream_presets.py::_preset_asset_path:90` | `admin.routers.dream` | import |
| `admin/routers/dream_presets.py::_preset_write_path:109` | `admin.routers.dream` | import |
| `admin/routers/dream_presets.py::_reload_dream_preset_registry:117` | `admin.routers.dream` | import |
| `admin/routers/dream_presets.py::get_standalone_dream_preset:127` | `admin.routers.dream` | import |
| `admin/routers/dream_presets.py::create_standalone_dream_preset:139` | `admin.routers.dream` | import |
| `admin/routers/dream_presets.py::put_standalone_dream_preset:161` | `admin.routers.dream` | import |
| `admin/routers/dream_presets.py::delete_standalone_dream_preset:181` | `admin.routers.dream` | import |
| `admin/routers/dream_presets.py::dream_settings_get:208` | `admin.routers.dream` | import |
| `admin/routers/dream_presets.py::dream_settings_patch:227` | `admin.routers.dream` | import |
| `admin/routers/dream_presets.py::get_standalone_dream_preset:128` | `_preset_asset_path` | read/call |
| `admin/routers/dream_presets.py::create_standalone_dream_preset:144` | `_preset_asset_path` | read/call |
| `admin/routers/dream_presets.py::create_standalone_dream_preset:145` | `_preset_write_path` | read/call |
| `admin/routers/dream_presets.py::create_standalone_dream_preset:150` | `_log_dream_write` | read/call |
| `admin/routers/dream_presets.py::create_standalone_dream_preset:151` | `_reload_dream_preset_registry` | read/call |
| `admin/routers/dream_presets.py::put_standalone_dream_preset:165` | `_preset_asset_path` | read/call |
| `admin/routers/dream_presets.py::put_standalone_dream_preset:168` | `_preset_write_path` | read/call |
| `admin/routers/dream_presets.py::put_standalone_dream_preset:170` | `_log_dream_write` | read/call |
| `admin/routers/dream_presets.py::put_standalone_dream_preset:172` | `_reload_dream_preset_registry` | read/call |
| `admin/routers/dream_presets.py::delete_standalone_dream_preset:182` | `_preset_asset_path` | read/call |
| `admin/routers/dream_presets.py::delete_standalone_dream_preset:193` | `_preset_write_path` | read/call |
| `admin/routers/dream_presets.py::delete_standalone_dream_preset:197` | `_reload_dream_preset_registry` | read/call |
| `admin/routers/dream_presets.py::dream_settings_get:209` | `_owner_uid` | read/call |
| `admin/routers/dream_presets.py::dream_settings_patch:228` | `_owner_uid` | read/call |
| `admin/routers/dream_presets.py::dream_settings_patch:229` | `resolve_requested_char_id` | read/call |
| `admin/routers/dream_presets.py::_valid_world_layer_values:65` | `_VALID_WORLD_LAYER_BUILTIN` | read/call |
| `admin/routers/dream_presets.py::_preset_asset_path:92` | `HTTPException` | read/call |
| `admin/routers/dream_presets.py::_preset_write_path:111` | `HTTPException` | read/call |
| `admin/routers/dream_presets.py::get_standalone_dream_preset:130` | `HTTPException` | read/call |
| `admin/routers/dream_presets.py::create_standalone_dream_preset:143` | `HTTPException` | read/call |
| `admin/routers/dream_presets.py::create_standalone_dream_preset:147` | `HTTPException` | read/call |
| `admin/routers/dream_presets.py::put_standalone_dream_preset:164` | `HTTPException` | read/call |
| `admin/routers/dream_presets.py::put_standalone_dream_preset:167` | `HTTPException` | read/call |
| `admin/routers/dream_presets.py::delete_standalone_dream_preset:187` | `HTTPException` | read/call |
| `admin/routers/dream_presets.py::delete_standalone_dream_preset:191` | `HTTPException` | read/call |
| `admin/routers/dream_presets.py::delete_standalone_dream_preset:195` | `HTTPException` | read/call |
| `admin/routers/dream_presets.py::dream_settings_patch:230` | `_active_dream_char_id` | read/call |
| `admin/routers/dream_presets.py::dream_settings_patch:236` | `HTTPException` | read/call |
| `admin/routers/dream_presets.py::dream_settings_patch:239` | `_ENUM_VALIDATORS` | read/call |
| `admin/routers/dream_presets.py::dream_settings_patch:245` | `_valid_world_layer_values` | read/call |
| `admin/routers/dream_presets.py::dream_settings_patch:274` | `HTTPException` | read/call |
| `admin/routers/dream_presets.py::_preset_write_path:112` | `get_paths` | read/call |
| `admin/routers/dream_presets.py::delete_standalone_dream_preset:186` | `HTTPException` | read/call |
| `admin/routers/dream_presets.py::dream_settings_get:211` | `_validated_live_char_id` | read/call |
| `admin/routers/dream_presets.py::dream_settings_patch:234` | `_PATCH_ALLOWED` | read/call |
| `admin/routers/dream_presets.py::_preset_asset_path:91` | `_SAFE_PRESET_RE` | read/call |
| `admin/routers/dream_presets.py::_preset_write_path:110` | `_SAFE_PRESET_RE` | read/call |
| `admin/routers/dream_presets.py::delete_standalone_dream_preset:184` | `get_paths` | read/call |
| `admin/routers/dream_presets.py::delete_standalone_dream_preset:190` | `_owner_uid` | read/call |
| `admin/routers/dream_presets.py::delete_standalone_dream_preset:198` | `get_paths` | read/call |
| `admin/routers/dream_presets.py::dream_settings_patch:236` | `_PATCH_ALLOWED` | read/call |
| `admin/routers/dream_presets.py::delete_standalone_dream_preset:190` | `_active_dream_char_id` | read/call |
| `admin/routers/dream_presets.py::dream_settings_patch:258` | `_SAFE_PRESET_RE` | read/call |
| `admin/routers/dream_rpg.py::rpg_correction:186` | `__import__('time')` | dynamic |
| `admin/routers/dream_scenarios.py::_validate_scenario_id:23` | `admin.routers.dream` | import |
| `admin/routers/dream_scenarios.py::_scenario_write_path:30` | `admin.routers.dream` | import |
| `admin/routers/dream_scenarios.py::_scenario_read_path:36` | `admin.routers.dream` | import |
| `admin/routers/dream_scenarios.py::_scenario_active:52` | `admin.routers.dream` | import |
| `admin/routers/dream_scenarios.py::_parse_and_validate_scenario_yaml:74` | `admin.routers.dream` | import |
| `admin/routers/dream_scenarios.py::_scenario_document_and_yaml:97` | `admin.routers.dream` | import |
| `admin/routers/dream_scenarios.py::list_dream_scenarios:120` | `admin.routers.dream` | import |
| `admin/routers/dream_scenarios.py::validate_dream_scenario:160` | `admin.routers.dream` | import |
| `admin/routers/dream_scenarios.py::get_dream_scenario:188` | `admin.routers.dream` | import |
| `admin/routers/dream_scenarios.py::create_dream_scenario:209` | `admin.routers.dream` | import |
| `admin/routers/dream_scenarios.py::update_dream_scenario:229` | `admin.routers.dream` | import |
| `admin/routers/dream_scenarios.py::delete_dream_scenario:253` | `admin.routers.dream` | import |
| `admin/routers/dream_scenarios.py::_scenario_read_path:38` | `_validate_scenario_id` | read/call |
| `admin/routers/dream_scenarios.py::_scenario_active:54` | `_owner_uid` | read/call |
| `admin/routers/dream_scenarios.py::_scenario_document_and_yaml:114` | `_parse_and_validate_scenario_yaml` | read/call |
| `admin/routers/dream_scenarios.py::get_dream_scenario:189` | `_scenario_read_path` | read/call |
| `admin/routers/dream_scenarios.py::get_dream_scenario:193` | `_parse_and_validate_scenario_yaml` | read/call |
| `admin/routers/dream_scenarios.py::get_dream_scenario:196` | `_canonical_scenario_yaml` | read/call |
| `admin/routers/dream_scenarios.py::create_dream_scenario:215` | `_scenario_document_and_yaml` | read/call |
| `admin/routers/dream_scenarios.py::create_dream_scenario:216` | `_scenario_write_path` | read/call |
| `admin/routers/dream_scenarios.py::create_dream_scenario:220` | `_log_dream_write` | read/call |
| `admin/routers/dream_scenarios.py::update_dream_scenario:230` | `_scenario_read_path` | read/call |
| `admin/routers/dream_scenarios.py::update_dream_scenario:233` | `_scenario_active` | read/call |
| `admin/routers/dream_scenarios.py::update_dream_scenario:236` | `_scenario_document_and_yaml` | read/call |
| `admin/routers/dream_scenarios.py::update_dream_scenario:237` | `_scenario_write_path` | read/call |
| `admin/routers/dream_scenarios.py::update_dream_scenario:241` | `_log_dream_write` | read/call |
| `admin/routers/dream_scenarios.py::delete_dream_scenario:254` | `_scenario_read_path` | read/call |
| `admin/routers/dream_scenarios.py::delete_dream_scenario:257` | `_scenario_active` | read/call |
| `admin/routers/dream_scenarios.py::_validate_scenario_id:25` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::_scenario_write_path:32` | `_validate_scenario_id` | read/call |
| `admin/routers/dream_scenarios.py::_parse_and_validate_scenario_yaml:83` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::_parse_and_validate_scenario_yaml:85` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::_scenario_document_and_yaml:115` | `_canonical_scenario_yaml` | read/call |
| `admin/routers/dream_scenarios.py::validate_dream_scenario:171` | `_parse_and_validate_scenario_yaml` | read/call |
| `admin/routers/dream_scenarios.py::validate_dream_scenario:172` | `_canonical_scenario_yaml` | read/call |
| `admin/routers/dream_scenarios.py::get_dream_scenario:191` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::create_dream_scenario:212` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::create_dream_scenario:213` | `_scenario_read_path` | read/call |
| `admin/routers/dream_scenarios.py::create_dream_scenario:214` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::update_dream_scenario:232` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::update_dream_scenario:234` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::delete_dream_scenario:256` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::delete_dream_scenario:258` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::delete_dream_scenario:262` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::_validate_scenario_id:24` | `_SAFE_SCRIPT_ID_RE` | read/call |
| `admin/routers/dream_scenarios.py::_parse_and_validate_scenario_yaml:79` | `_normalize_field_outline_scenario_yaml` | read/call |
| `admin/routers/dream_scenarios.py::_parse_and_validate_scenario_yaml:81` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::_parse_and_validate_scenario_yaml:92` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::_scenario_document_and_yaml:101` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::_scenario_document_and_yaml:104` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::_scenario_document_and_yaml:111` | `_canonical_scenario_yaml` | read/call |
| `admin/routers/dream_scenarios.py::validate_dream_scenario:167` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::validate_dream_scenario:179` | `_scenario_document_and_yaml` | read/call |
| `admin/routers/dream_scenarios.py::validate_dream_scenario:181` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::_scenario_document_and_yaml:110` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::validate_dream_scenario:175` | `HTTPException` | read/call |
| `admin/routers/dream_scenarios.py::validate_dream_scenario:178` | `HTTPException` | read/call |
| `admin/routers/dream_sessions.py::_requested_dream_id:21` | `admin.routers.dream` | import |
| `admin/routers/dream_sessions.py::_set_wake_observation:41` | `admin.routers.dream` | import |
| `admin/routers/dream_sessions.py::dream_enter:54` | `admin.routers.dream` | import |
| `admin/routers/dream_sessions.py::dream_chat:93` | `admin.routers.dream` | import |
| `admin/routers/dream_sessions.py::dream_exit:183` | `admin.routers.dream` | import |
| `admin/routers/dream_sessions.py::dream_wake:204` | `admin.routers.dream` | import |
| `admin/routers/dream_sessions.py::dream_resume:353` | `admin.routers.dream` | import |
| `admin/routers/dream_sessions.py::_requested_dream_id:29` | `_validated_dream_id` | read/call |
| `admin/routers/dream_sessions.py::dream_enter:55` | `_owner_uid` | read/call |
| `admin/routers/dream_sessions.py::dream_enter:60` | `_VALID_DREAM_MODE` | read/call |
| `admin/routers/dream_sessions.py::dream_enter:72` | `resolve_requested_char_id` | read/call |
| `admin/routers/dream_sessions.py::dream_chat:98` | `_owner_uid` | read/call |
| `admin/routers/dream_sessions.py::dream_exit:184` | `_owner_uid` | read/call |
| `admin/routers/dream_sessions.py::dream_wake:205` | `_owner_uid` | read/call |
| `admin/routers/dream_sessions.py::dream_wake:225` | `_requested_dream_id` | read/call |
| `admin/routers/dream_sessions.py::dream_wake:333` | `_set_wake_observation` | read/call |
| `admin/routers/dream_sessions.py::dream_resume:354` | `_owner_uid` | read/call |
| `admin/routers/dream_sessions.py::dream_resume:361` | `_requested_dream_id` | read/call |
| `admin/routers/dream_sessions.py::_requested_dream_id:25` | `HTTPException` | read/call |
| `admin/routers/dream_sessions.py::dream_enter:61` | `HTTPException` | read/call |
| `admin/routers/dream_sessions.py::dream_enter:71` | `HTTPException` | read/call |
| `admin/routers/dream_sessions.py::dream_enter:73` | `_active_dream_char_id` | read/call |
| `admin/routers/dream_sessions.py::dream_enter:81` | `HTTPException` | read/call |
| `admin/routers/dream_sessions.py::dream_chat:96` | `HTTPException` | read/call |
| `admin/routers/dream_sessions.py::dream_chat:107` | `HTTPException` | read/call |
| `admin/routers/dream_sessions.py::dream_chat:125` | `HTTPException` | read/call |
| `admin/routers/dream_sessions.py::dream_wake:214` | `HTTPException` | read/call |
| `admin/routers/dream_sessions.py::dream_wake:246` | `_set_wake_observation` | read/call |
| `admin/routers/dream_sessions.py::dream_wake:271` | `_set_wake_observation` | read/call |
| `admin/routers/dream_sessions.py::dream_wake:289` | `_set_wake_observation` | read/call |
| `admin/routers/dream_sessions.py::dream_wake:316` | `_set_wake_observation` | read/call |
| `admin/routers/dream_sessions.py::dream_resume:360` | `HTTPException` | read/call |
| `admin/routers/dream_sessions.py::dream_resume:364` | `HTTPException` | read/call |
| `admin/routers/dream_sessions.py::_set_wake_observation:47` | `time` | read/call |
| `admin/routers/dream_sessions.py::dream_wake:238` | `HTTPException` | read/call |
| `admin/routers/dream_sessions.py::dream_wake:245` | `HTTPException` | read/call |
| `admin/routers/dream_sessions.py::dream_wake:315` | `logger` | read/call |
| `admin/routers/dream_sessions.py::dream_resume:367` | `HTTPException` | read/call |
| `admin/routers/dream_sessions.py::dream_resume:381` | `logger` | read/call |
| `admin/routers/dream_sessions.py::dream_chat:148` | `logger` | read/call |
| `admin/routers/dream_sessions.py::dream_chat:157` | `logger` | read/call |
| `admin/routers/dream_sessions.py::dream_chat:172` | `logger` | read/call |
| `admin/routers/dream_sessions.py::dream_resume:378` | `time` | read/call |
| `admin/routers/dream_sessions.py::dream_enter:63` | `_VALID_DREAM_MODE` | read/call |
| `admin/routers/dream_worlds.py::_validate_world_name:31` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::_world_dir:40` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::_world_write_dir:49` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::_materialize_world_for_write:56` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::_preset_path:71` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::_dream_active_referencing_world:82` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::_reset_world_layer_setting_if:96` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::_ensure_default_world_template_seeded:112` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::list_dream_worlds:122` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::create_dream_world:134` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::rename_dream_world:164` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::delete_dream_world:200` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::get_dream_lorebook:226` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::_write_dream_lorebook:243` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::add_dream_lore_entry:250` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::update_dream_lore_entry:278` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::delete_dream_lore_entry:307` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::get_dream_preset:323` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::put_dream_preset:332` | `admin.routers.dream` | import |
| `admin/routers/dream_worlds.py::_world_write_dir:50` | `_validate_world_name` | read/call |
| `admin/routers/dream_worlds.py::_materialize_world_for_write:57` | `_world_dir` | read/call |
| `admin/routers/dream_worlds.py::_materialize_world_for_write:58` | `_world_write_dir` | read/call |
| `admin/routers/dream_worlds.py::_materialize_world_for_write:65` | `_log_dream_write` | read/call |
| `admin/routers/dream_worlds.py::_dream_active_referencing_world:84` | `_owner_uid` | read/call |
| `admin/routers/dream_worlds.py::_reset_world_layer_setting_if:98` | `_owner_uid` | read/call |
| `admin/routers/dream_worlds.py::_reset_world_layer_setting_if:99` | `_active_dream_char_id` | read/call |
| `admin/routers/dream_worlds.py::create_dream_world:138` | `_validate_world_name` | read/call |
| `admin/routers/dream_worlds.py::create_dream_world:147` | `_ensure_default_world_template_seeded` | read/call |
| `admin/routers/dream_worlds.py::create_dream_world:151` | `_log_dream_write` | read/call |
| `admin/routers/dream_worlds.py::rename_dream_world:165` | `_validate_world_name` | read/call |
| `admin/routers/dream_worlds.py::rename_dream_world:169` | `_validate_world_name` | read/call |
| `admin/routers/dream_worlds.py::rename_dream_world:171` | `_world_dir` | read/call |
| `admin/routers/dream_worlds.py::rename_dream_world:174` | `_world_write_dir` | read/call |
| `admin/routers/dream_worlds.py::rename_dream_world:178` | `_dream_active_referencing_world` | read/call |
| `admin/routers/dream_worlds.py::rename_dream_world:186` | `_reset_world_layer_setting_if` | read/call |
| `admin/routers/dream_worlds.py::delete_dream_world:201` | `_validate_world_name` | read/call |
| `admin/routers/dream_worlds.py::delete_dream_world:203` | `_world_dir` | read/call |
| `admin/routers/dream_worlds.py::delete_dream_world:210` | `_world_write_dir` | read/call |
| `admin/routers/dream_worlds.py::delete_dream_world:213` | `_dream_active_referencing_world` | read/call |
| `admin/routers/dream_worlds.py::delete_dream_world:218` | `_reset_world_layer_setting_if` | read/call |
| `admin/routers/dream_worlds.py::get_dream_lorebook:236` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::add_dream_lore_entry:272` | `_write_dream_lorebook` | read/call |
| `admin/routers/dream_worlds.py::update_dream_lore_entry:301` | `_write_dream_lorebook` | read/call |
| `admin/routers/dream_worlds.py::delete_dream_lore_entry:317` | `_write_dream_lorebook` | read/call |
| `admin/routers/dream_worlds.py::get_dream_preset:324` | `_preset_path` | read/call |
| `admin/routers/dream_worlds.py::put_dream_preset:334` | `_preset_path` | read/call |
| `admin/routers/dream_worlds.py::put_dream_preset:337` | `_log_dream_write` | read/call |
| `admin/routers/dream_worlds.py::put_dream_preset:339` | `_reload_dream_preset_registry` | read/call |
| `admin/routers/dream_worlds.py::_validate_world_name:33` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::_validate_world_name:35` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::_world_dir:42` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::_materialize_world_for_write:62` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::_preset_path:73` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::_ensure_default_world_template_seeded:116` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::create_dream_world:137` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::create_dream_world:145` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::rename_dream_world:168` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::rename_dream_world:173` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::rename_dream_world:176` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::rename_dream_world:179` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::rename_dream_world:181` | `_world_write_dir` | read/call |
| `admin/routers/dream_worlds.py::rename_dream_world:182` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::delete_dream_world:208` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::delete_dream_world:212` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::delete_dream_world:214` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::get_dream_lorebook:228` | `_world_dir` | read/call |
| `admin/routers/dream_worlds.py::_write_dream_lorebook:245` | `_materialize_world_for_write` | read/call |
| `admin/routers/dream_worlds.py::add_dream_lore_entry:252` | `_materialize_world_for_write` | read/call |
| `admin/routers/dream_worlds.py::add_dream_lore_entry:261` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::add_dream_lore_entry:263` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::update_dream_lore_entry:280` | `_materialize_world_for_write` | read/call |
| `admin/routers/dream_worlds.py::update_dream_lore_entry:282` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::update_dream_lore_entry:285` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::delete_dream_lore_entry:309` | `_materialize_world_for_write` | read/call |
| `admin/routers/dream_worlds.py::delete_dream_lore_entry:311` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::delete_dream_lore_entry:314` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::_validate_world_name:34` | `_RESERVED_WORLD_NAMES` | read/call |
| `admin/routers/dream_worlds.py::_world_write_dir:51` | `get_paths` | read/call |
| `admin/routers/dream_worlds.py::_preset_path:75` | `get_paths` | read/call |
| `admin/routers/dream_worlds.py::create_dream_world:144` | `_world_dir` | read/call |
| `admin/routers/dream_worlds.py::rename_dream_world:175` | `_world_dir` | read/call |
| `admin/routers/dream_worlds.py::delete_dream_world:207` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::get_dream_lorebook:234` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::get_dream_lorebook:239` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::update_dream_lore_entry:291` | `HTTPException` | read/call |
| `admin/routers/dream_worlds.py::put_dream_preset:335` | `get_paths` | read/call |
| `admin/routers/dream_worlds.py::_validate_world_name:32` | `_SAFE_WORLD_RE` | read/call |
| `admin/routers/dream_worlds.py::_world_dir:41` | `_SAFE_WORLD_RE` | read/call |
| `admin/routers/dream_worlds.py::_preset_path:72` | `_SAFE_WORLD_RE` | read/call |
| `admin/routers/dream_worlds.py::_preset_path:77` | `get_paths` | read/call |
| `admin/routers/dream_worlds.py::_world_dir:45` | `get_paths` | read/call |
| `admin/routers/dream_worlds.py::delete_dream_world:205` | `get_paths` | read/call |
| `admin/routers/dream_worlds.py::delete_dream_world:220` | `get_paths` | read/call |

## Ownership and regression matrix

| Opening edge / reason | Unique owner after migration | Regression |
|---|---|---|
| dispatcher → registry → dispatcher; domain implementation lookup, mutable registry replacement | registry initializes one identity; definitions import actual domain owners; scoped temporary overrides | modularization, MCP lifecycle, schema snapshots |
| policy / exposure / execution → facade for config / grants / result / helpers | policy admission, exposure presentation, invoker trusted binding, outcome classification; direct imports from actual owning modules | production execution, permissions, schema, tool loop, result / ACK / audit |
| owner service → chat router for probe monkeypatch | owner_chat.prepare; core callers invoke it directly | mobile/owner/desktop receipts, cancellation, frozen role, streaming failure |
| dream child → dream facade for shared helpers, paths and peers | dream_common HTTP validation; domain routers own HTTP helpers; core/dream owns state and authoring | Dream CRUD / hard exit / wake / isolation and OpenAPI |

## Call graphs

Before: facade → implementation → facade (runtime lookup and test patch seams).

After target:
```text
tool_dispatcher (stable API) → registry / exposure / execution
registry → domain definitions + existing register_tools; MCP → same registry
exposure → policy; execution → policy / validation / invoker / outcome
invoker → registered domain adapter → domain implementation
chat/mobile/owner_turn → owner_chat.service → prepare / generation / delivery
prepare → pretool_router → pipeline; delivery → turn_sink → channels
dream (composition) → sessions / archives / observability / presets / worlds / scenarios
Dream children → dream_common / core.dream state, authoring and layered paths
```

## Stage evidence

Baseline and each stage are recorded below after execution. Full synthetic snapshots/logs: ignored `.tmp/ar02-*`. No user-private data is an input.

### A — registry and domain ownership

- Opening targeted run: **165 passed / 0 failed / 0 skipped**, Python 3.12.13.
- Registry/schema/probe/OpenAPI/routes snapshots are byte-equivalent as JSON values:
  163 entries, 96 default schemas, 4515-character probe, 65 mounted routes.
  Registry hash `40c9fbf7ca1856c00f988944babd1a882e1e051b46f10df83fa49c1ebe2857f8`;
  OpenAPI hash `6d7ba2b70a6048dfb09d650661d6f6cfb8bd1cfe5bacf17035753e95d855952f`.
- Definitions now import actual owners, including their own adapters/functions.
  Action transport mapping belongs to devices; legacy active-character fallback
  belongs to context. No copied business state or new service container.
- Registry owns composition/identity and explicit `temporary_registry`; reload
  and repeated initialization preserve entries and extensions. MCP mutates it
  directly. Full-attribute test replacement migrated to identity-preserving
  overrides; per-test registry teardown prevents dynamic MCP state leakage.
- First expanded run: 118 passed / 5 failed (test isolation leaked entries);
  repaired teardown, same group 123 passed; additional lifecycle test included
  in final A run. Failure assertions were not loosened.
- Commands: `python -m pytest -q tests/test_tool_modularization.py
  tests/test_mcp_client.py tests/test_tool_schema_portable.py
  tests/test_execute_structured_production.py tests/test_action_trace.py
  tests/test_tool_probe_reader_scope.py tests/test_mcp_local_effect_policy.py
  tests/test_mcp_optional_metadata.py --tb=short`.
- A matrix: registry→dispatcher **0**, definitions→dispatcher **0**;
  runtime policy/exposure/execution/outcome and Dream/owner back edges remain
  scheduled for B/C/D. The facade retains same-object public aliases.
- Final A run: **124 passed / 0 failed / 0 skipped**. The new synthetic MCP
  test explicitly supplies real registration metadata (`mcp_server/mcp_tool`);
  missing metadata was correctly denied during test development.

### B — runtime direction and responsibility

- Runtime→dispatcher **0** and definitions→dispatcher **0** (imports, local
  imports and dynamic lookups). The single dictionary is owned by `state.py`;
  registry alone composes it. This separates policy reads from composition and
  avoids a definitions→policy→composer initialization cycle.
- `InvocationScope` carries trusted identity only. Policy owns ordered admission
  and shares principal/grant checks with schema exposure. Validation recurses
  locally. Invoker uses the registered adapter directly, with no domain scan.
  Explicit builtin override inheritance is resolved before calls.
- Outcome owns validity/status/observer classification and privacy. Execution
  retains one deliberate per-name redirect: phone video invitations re-enter
  the complete desktop admission path. This must remain orchestration because
  invoking a domain adapter directly would bypass the destination gates.
- Signature/permission helpers belong to the definitions; mutable config/name
  integration boundaries are read from their actual module. Tests now patch
  those owners. Capability registry/policy were migrated too, removing their
  indirect facade cycle.
- Minimal security fix: failure diagnostics no longer log raw arguments or
  exception bodies, which could expose private memory, mail or MCP secrets.
  Error type/origin/tool/count remain. Safe outward business-error handling is
  unchanged. A short mutation restoring raw exception logging failed the
  private-text absence assertion; restored implementation passes.
- Main tools command (A group plus `test_tool_loop`, `test_pretool_router`,
  `test_tool_read_log_bypass`, `-n auto`): **220 passed / 0 failed / 0 skipped**.
  First B run caught an incomplete removed-loop fragment (28 failures); fixed
  the SyntaxError and reran the identical group successfully.
- Domain command: `python -m pytest -n auto -q
  tests/test_device_file_agent.py tests/test_computer_use.py
  tests/test_browser_workbench.py tests/test_gmail_messages.py
  tests/test_gmail_pending.py tests/test_qzone.py tests/test_qzone_events.py
  tests/test_memory_event_read_tools.py tests/test_memory_dossier_tools.py
  tests/test_tool_permission_defaults.py tests/test_tool_result.py
  tests/test_tool_result_pins.py tests/test_action_routing.py
  tests/test_phone_control_tool.py tests/test_diary_backfill.py
  tests/test_document_notes.py tests/test_fs_browse.py tests/test_chat_artifacts.py
  --tb=short`: **253 passed / 0 failed / 0 skipped**.
- Registry/reload/scope/privacy group: **3 passed**. Snapshot is unchanged
  (all registered metadata/order, schema, probe, OpenAPI, mounted route order).
- B matrix: runtime→facade **0**, domain definitions→facade **0**;
  Dream and owner reverse calls still await C/D. No client/config/UI change.

### C — Dream layering

- All Dream children→dream facade edges: **0**. `dream_common` owns HTTP
  owner/character validation and write-source diagnostics; domain children
  import their actual peers. Core owns the Dream ID grammar and wake/resume
  state transitions in `session_control`, returning typed domain errors that
  routers translate to the identical HTTP status/detail.
- `scenario_authoring` now owns YAML/document validation and canonical writes;
  `world_authoring` owns copy/materialize/rename/delete/preset writes. HTTP
  authorization/validation and canonical-vs-legacy checks remain in adapters.
  Existing state storage, layered path providers and hard-exit chokepoint are
  reused; there is no second state machine or root path.
- Facade includes and old callable exports remain outward-only. Tests patch
  `dream_common` / domain owners. Endpoint descriptions were restored after
  an initial OpenAPI comparison caught two lost docstrings.
- Command: `python -m pytest -n auto -q tests/test_dream_invariants.py
  tests/test_dream_scenario_crud.py tests/test_dream_world_management.py
  tests/test_dream_wake_confirmation.py tests/test_dream_isolation.py
  tests/test_dream_guard_fail_closed.py tests/test_dream_archive_operations.py
  tests/test_dream_flow_entries.py tests/test_dream_ui_endpoints.py
  tests/test_dream_state_projection.py tests/test_dream_session_char_scope.py
  tests/test_dream_rpg_api.py tests/test_dream_settings_ownership.py
  tests/test_activity_pseudo_stream.py tests/test_dream_isolation_guard.py
  --tb=short`: **150 passed / 0 failed / 0 skipped**.
  Initial 149 passed / 1 failed caught an aliased path import; fixed and reran
  the complete same group after all authoring changes.
- Final C snapshot matches all opening values, including exact OpenAPI and
  route registration order/defaults. Core modules have no Admin imports.
- Matrix: runtime→dispatcher **0**, definitions→dispatcher **0**,
  Dream children→dream **0**; owner service still awaits D.

### D — owner chat direction and per-turn contracts

- Owner core→chat router **0**. Service directly calls `owner_chat.prepare`;
  prepare owns the sole pretool adapter and accepts an explicit request-local
  probe override. Private test patches moved from Router to prepare.
- `OwnerChatTurn` / `PreparedTurn` / `GeneratedTurn` have concrete types for
  frozen scope, event identity, SessionState, booleans, timing, tool result and
  stream identity. The public `run_owner_chat_turn` signature is unchanged.
  One frozen turn object now crosses the locked service phases, eliminating a
  redundant 27-field unpack/repack. No new shared state or message writer.
- Removed only the body after `/chat`'s unconditional 410 and its two unused
  constants. Repository callers were checked first; mounted route, response,
  dependency, summary and description are retained exactly.
- Regression command: `python -m pytest -n auto -q
  tests/test_mobile_chat_channel.py tests/test_owner_turn_api_contract.py
  tests/test_desktop_chat_receipts.py tests/test_llm_force_stream.py
  tests/test_tool_grounding.py tests/test_reality_output_guard.py
  tests/test_short_term_history_scope.py tests/test_session_scope.py
  tests/test_output_segment_enforcer.py tests/test_chat_media.py
  tests/test_external_tool_contract_p0.py tests/test_scheduler_active_window.py
  tests/test_perceive_event.py --tb=short` (result recorded after completion).
- Final D run: **308 passed / 0 failed / 0 skipped** (193.61 seconds).
- These actual ingress tests exercise owner/desktop/mobile, stream protocol
  rejection/upstream errors/cancellation, gather sibling cleanup, attachments,
  frozen roles, receipts and canonical writes. QQ/autonomy keep their existing
  Pipeline/turn_sink chain. No desktop/mobile repo or protocol changes.
- D matrix: all three forbidden directions **0**; all six snapshots unchanged.
