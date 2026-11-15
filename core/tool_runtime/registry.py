"""Single builtin registry construction; MCP mutates the same returned object."""

def build_registry(registry):
    from core.tools import information_definitions, memory_definitions, devices_definitions, files_agents_definitions, life_media_definitions
    for module in (information_definitions, memory_definitions, devices_definitions, files_agents_definitions, life_media_definitions):
        module.register_tools(registry)
    from core.tool_dispatcher import _register_artifact_tools
    _register_artifact_tools(registry)
    from core.tool_dispatcher import _register_drinking_tools
    _register_drinking_tools(registry)
    from core.tool_dispatcher import _register_self_tools
    _register_self_tools(registry)
    from core.tool_dispatcher import _register_reminder_tools
    _register_reminder_tools(registry)
    from core.tool_dispatcher import _register_qzone_tools
    _register_qzone_tools(registry)
    from core.tool_dispatcher import _register_listening_tools
    _register_listening_tools(registry)
    from core.tool_dispatcher import _register_image_gen_tools
    _register_image_gen_tools(registry)
    from core.tool_dispatcher import _gmail_tools
    _gmail_tools.register(registry)
    from core.tool_dispatcher import _device_file_tools
    _device_file_tools.register(registry)
    from core.tool_dispatcher import _intent_tools
    _intent_tools.register(registry)
    for module in (information_definitions, memory_definitions, devices_definitions, files_agents_definitions, life_media_definitions):
        module.register_invokers(registry)
    # Existing order is a presentation contract; new domain entries append naturally.
    order = (
        'get_time', 'weather', 'device_shutdown', 'device_sleep', 'web_search',
        'read_life_records', 'read_xiaohongshu', 'continue_xiaohongshu_comments', 'read_xiaohongshu_images', 'read_xiaohongshu_author_posts',
        'read_food_preferences', 'query_activity_records', 'read_message_context', 'pin_tool_result', 'unpin_tool_result',
        'list_tool_result_pins', 'save_quote', 'search_quotes', 'read_quote', 'write_quote_note',
        'backfill_diary', 'read_diary', 'read_watch', 'read_hds_heart_rate', 'search_diary',
        'desktop_minimize', 'desktop_open_url', 'desktop_play_pause', 'desktop_notify', 'toy_invite',
        'dream_invite', 'play_song', 'get_profile', 'get_episodic', 'read_long_user_message',
        'search_memory_dossiers', 'read_memory_dossier', 'search_dossier_events', 'update_memory_dossier', 'get_memory_consolidation_status',
        'request_memory_consolidation', 'search_documents', 'read_document', 'read_document_notes', 'write_document_note',
        'search_character_notes', 'search_events', 'expand_event_window', 'get_related_events', 'revise_memory',
        'forget_episodic', 'clear_midterm', 'revise_user_profile', 'exit_yandere', 'agent_session_start',
        'agent_session_status', 'agent_session_control', 'computer_use_start', 'computer_use_status', 'computer_use_cancel',
        'phone_control_start', 'water_garden', 'observe_user_screen', 'observe_video_call_camera', 'invite_video_call',
        'peek_screen_content', 'toy_vibrate', 'toy_stop', 'toy_pattern', 'reread_image',
        'toy_job_status', 'read_toy_file', 'write_toy_file', 'fs_list', 'fs_read',
        'workspace_list', 'workspace_read', 'workspace_create', 'workspace_update', 'workspace_delete',
        'workspace_undo', 'process_run', 'start_agent_task', 'get_agent_task', 'cancel_agent_task',
        'browser_automation', 'manage_self_capability', 'list_self_capabilities', 'read_self_action_history', 'write_artifact',
        'update_artifact', 'read_artifact', 'list_artifacts', 'drink_with_user', 'self_db_tables',
        'self_db_create_table', 'self_db_insert', 'self_db_query', 'self_db_update', 'self_db_delete',
        'self_db_drop_table', 'self_tool_define', 'self_tool_list', 'self_tool_run', 'self_list',
        'self_read', 'self_create', 'self_update', 'self_move', 'self_delete',
        'self_restore', 'list_reminders', 'get_reminder', 'add_reminder', 'update_reminder',
        'cancel_reminder', 'restore_reminder', 'qzone_get_feeds', 'qzone_get_posts', 'qzone_get_comments',
        'qzone_publish', 'qzone_comment', 'qzone_set_like', 'qzone_delete_post', 'get_listening_state',
        'get_listening_queue', 'get_listening_history', 'get_track_note', 'write_track_note', 'choose_next_track',
        'get_ambient_state', 'pin_character_image_reference', 'generate_image', 'gmail_list_messages', 'gmail_search_messages',
        'gmail_read_message', 'gmail_list_pending', 'gmail_mark_handled', 'gmail_reopen_pending', 'gmail_list_labels',
        'gmail_send_message', 'gmail_reply_message', 'device_workspace_list', 'device_file_task_start', 'device_file_task_status',
        'device_file_task_control', 'device_file_task_cancel', 'browser_session_start', 'browser_session_action', 'browser_snapshot',
        'browser_screenshot', 'browser_console', 'browser_visual_review', 'browser_session_status', 'browser_session_cancel',
        'device_file_list_dir', 'device_file_stat', 'device_file_find_name', 'device_file_search_text', 'device_file_read',
        'list_character_intents', 'propose_character_intent', 'revise_character_intent',
    )
    ordered = {name: registry[name] for name in order}
    ordered.update(registry)
    registry.clear()
    registry.update(ordered)
