PAYLOAD_ONLY_INPUT_SCHEMA = {
    "type": "object",
    "required": ["payload"],
    "properties": {
        "payload": {"type": "object"},
    },
    "additionalProperties": False,
}

GROUP_KNOWLEDGE_REPLY_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["action", "content", "reason", "confidence"],
    "properties": {
        "action": {"type": "string", "enum": ["reply", "out_of_scope"]},
        "content": {"type": "string"},
        "reason": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "additionalProperties": True,
    "allOf": [
        {
            "if": {
                "required": ["action"],
                "properties": {"action": {"const": "reply"}},
            },
            "then": {
                "properties": {"content": {"type": "string", "minLength": 1}},
            },
        },
        {
            "if": {
                "required": ["action"],
                "properties": {"action": {"const": "out_of_scope"}},
            },
            "then": {
                "properties": {"content": {"const": ""}},
            },
        },
    ],
}

CHAT_PROACTIVE_REMINDER_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["should_send", "target_userids", "quote_msgid", "content", "confidence"],
    "properties": {
        "should_send": {"type": "boolean"},
        "target_userids": {"type": "array", "items": {"type": "string"}},
        "quote_msgid": {"type": "string"},
        "content": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "additionalProperties": True,
    "allOf": [
        {
            "if": {
                "required": ["should_send"],
                "properties": {"should_send": {"const": True}},
            },
            "then": {
                "properties": {
                    "target_userids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "quote_msgid": {"type": "string", "minLength": 1},
                    "content": {"type": "string", "minLength": 1},
                },
            },
        }
    ],
}

CONVERSATION_BOUNDARY_DETECTION_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["status", "split_positions", "confidence", "error"],
    "properties": {
        "status": {"type": "string", "enum": ["success", "failed"]},
        "split_positions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "after_msgid",
                    "before_msgid",
                    "reason_type",
                    "reason",
                    "confidence",
                ],
                "properties": {
                    "after_msgid": {"type": "string", "minLength": 1},
                    "before_msgid": {"type": "string", "minLength": 1},
                    "reason_type": {
                        "type": "string",
                        "enum": [
                            "time_gap",
                            "intent_shift",
                            "topic_shift",
                            "task_closed",
                            "manual_hint",
                            "other",
                        ],
                    },
                    "reason": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "additionalProperties": True,
            },
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "error": {"type": ["object", "null"]},
    },
    "additionalProperties": True,
}

USER_PROFILE_UPDATE_OUTPUT_SCHEMA = {
    "type": "object",
    "required": [
        "userid",
        "profile_action",
        "updated_profile",
        "changes",
        "evidence",
        "confidence",
    ],
    "properties": {
        "userid": {"type": "string"},
        "profile_action": {
            "type": "string",
            "enum": ["create", "update", "no_change"],
        },
        "updated_profile": {
            "type": "object",
            "required": ["summary", "facts"],
            "properties": {
                "summary": {"type": "string"},
                "facts": {"type": "array", "items": {"type": "object"}},
            },
            "additionalProperties": True,
        },
        "changes": {
            "type": "object",
            "required": ["facts_added", "facts_updated", "facts_retired"],
            "properties": {
                "facts_added": {"type": "array", "items": {"type": "object"}},
                "facts_updated": {"type": "array", "items": {"type": "object"}},
                "facts_retired": {"type": "array", "items": {"type": "object"}},
            },
            "additionalProperties": True,
        },
        "evidence": {
            "type": "object",
            "required": ["conversation_no", "msgids"],
            "properties": {
                "conversation_no": {"type": "string"},
                "msgids": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": True,
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "additionalProperties": True,
}

REPLY_GENERATION_INPUT_SCHEMA = {
    "type": "object",
    "required": [
        "reply_scene",
        "chatid",
        "source_msgid",
        "request_userid",
        "target_userids",
        "user_message",
        "reply_instruction",
        "evidence_msgids",
        "recent_messages",
        "conversation_summary",
        "user_profile",
        "runtime",
    ],
    "properties": {
        "reply_scene": {"type": "string", "enum": ["mention", "proactive", "manual"]},
        "chatid": {"type": "string", "minLength": 1},
        "source_msgid": {"type": "string", "minLength": 1},
        "request_userid": {"type": ["string", "null"]},
        "target_userids": {"type": "array", "items": {"type": "string"}},
        "user_message": {"type": ["string", "null"]},
        "reply_instruction": {"type": "string"},
        "evidence_msgids": {"type": "array", "items": {"type": "string"}},
        "recent_messages": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["msgid", "userid", "content", "create_time"],
                "properties": {
                    "msgid": {"type": "string", "minLength": 1},
                    "userid": {"type": ["string", "null"]},
                    "content": {"type": ["string", "null"]},
                    "create_time": {"type": "string", "minLength": 1},
                },
                "additionalProperties": True,
            },
        },
        "conversation_summary": {"type": ["object", "null"]},
        "user_profile": {"type": ["object", "null"]},
        "runtime": {"type": "object"},
    },
    "additionalProperties": True,
}

REPLY_GENERATION_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["action", "reply", "metadata", "confidence"],
    "properties": {
        "action": {"type": "string", "enum": ["reply", "ignore"]},
        "reply": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "required": ["reply_type", "content"],
                    "properties": {
                        "reply_type": {"type": "string", "enum": ["markdown", "text"]},
                        "content": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
            ]
        },
        "metadata": {
            "type": "object",
            "required": ["reply_scene", "intent_type", "evidence_msgids"],
            "properties": {
                "reply_scene": {"type": "string"},
                "intent_type": {"type": "string"},
                "evidence_msgids": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": True,
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "additionalProperties": True,
    "allOf": [
        {
            "if": {
                "required": ["action"],
                "properties": {"action": {"const": "reply"}},
            },
            "then": {
                "required": ["reply"],
                "properties": {
                    "reply": {
                        "type": "object",
                        "required": ["content"],
                        "properties": {"content": {"type": "string", "minLength": 1}},
                    }
                },
            },
        }
    ],
}

INTENT_DETECTION_INPUT_SCHEMA = {
    "type": "object",
    "required": [
        "trigger_source",
        "chatid",
        "start_time",
        "end_time",
        "matched_keywords",
        "known_users",
        "messages",
        "existing_actions",
    ],
    "properties": {
        "trigger_source": {
            "type": "string",
            "enum": ["keyword_scan", "schedule_scan", "manual"],
        },
        "chatid": {"type": "string", "minLength": 1},
        "start_time": {"type": "string", "minLength": 1},
        "end_time": {"type": "string", "minLength": 1},
        "matched_keywords": {"type": "array", "items": {"type": "string"}},
        "messages": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["message_id", "msgid", "chatid", "userid", "content", "create_time"],
                "properties": {
                    "message_id": {"type": "string"},
                    "msgid": {"type": "string", "minLength": 1},
                    "chatid": {"type": "string", "minLength": 1},
                    "userid": {"type": ["string", "null"]},
                    "content": {"type": ["string", "null"]},
                    "create_time": {"type": "string", "minLength": 1},
                },
                "additionalProperties": True,
            },
        },
        "known_users": {"type": "array", "items": {"type": "string"}},
        "existing_actions": {"type": "array"},
    },
    "additionalProperties": True,
}

INTENT_DETECTION_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["actions"],
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "action_type",
                    "intent_type",
                    "target_userids",
                    "evidence_msgids",
                    "reason",
                    "priority",
                    "confidence",
                ],
                "properties": {
                    "action_type": {
                        "type": "string",
                        "enum": ["reply", "create_task", "ignore"],
                    },
                    "intent_type": {"type": "string", "minLength": 1},
                    "target_userids": {"type": "array", "items": {"type": "string"}},
                    "evidence_msgids": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                    "reply_instruction": {"type": "string"},
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "urgent"],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "additionalProperties": True,
            },
        }
    },
    "additionalProperties": True,
}

MENTION_REPLY_INPUT_SCHEMA = REPLY_GENERATION_INPUT_SCHEMA
MENTION_REPLY_OUTPUT_SCHEMA = REPLY_GENERATION_OUTPUT_SCHEMA
SCHEDULED_INTENT_DETECTION_INPUT_SCHEMA = INTENT_DETECTION_INPUT_SCHEMA
SCHEDULED_INTENT_DETECTION_OUTPUT_SCHEMA = INTENT_DETECTION_OUTPUT_SCHEMA

CONVERSATION_SEGMENTATION_INPUT_SCHEMA = {
    "type": "object",
    "required": ["chatid", "window", "messages", "candidate_boundaries"],
    "properties": {
        "chatid": {"type": "string", "minLength": 1},
        "window": {
            "type": "object",
            "required": ["start_time", "end_time"],
            "properties": {
                "start_time": {"type": "string"},
                "end_time": {"type": "string"},
            },
        },
        "messages": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["message_id", "msgid", "userid", "create_time"],
                "properties": {
                    "message_id": {"type": "string"},
                    "msgid": {"type": "string"},
                    "userid": {"type": ["string", "null"]},
                    "content": {"type": ["string", "null"]},
                    "create_time": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
        "candidate_boundaries": {"type": "array"},
        "previous_conversation": {},
    },
    "additionalProperties": True,
}

CONVERSATION_SEGMENTATION_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["segments"],
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "title",
                    "start_msgid",
                    "end_msgid",
                    "summary",
                    "keywords",
                    "participants",
                    "confidence",
                ],
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "start_msgid": {"type": "string", "minLength": 1},
                    "end_msgid": {"type": "string", "minLength": 1},
                    "summary": {"type": "string", "minLength": 1},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "participants": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "additionalProperties": True,
            },
        }
    },
    "additionalProperties": True,
}

USER_PROFILE_ANALYSIS_INPUT_SCHEMA = {
    "type": "object",
    "required": [
        "userid",
        "profile_version",
        "recent_messages",
        "conversation_summaries",
        "current_profile",
        "statistics",
    ],
    "properties": {
        "userid": {"type": "string", "minLength": 1},
        "profile_version": {"type": "integer", "minimum": 1},
        "recent_messages": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["message_id", "msgid", "chatid", "userid", "create_time"],
                "properties": {
                    "message_id": {"type": "string"},
                    "msgid": {"type": "string", "minLength": 1},
                    "chatid": {"type": "string", "minLength": 1},
                    "userid": {"type": "string", "minLength": 1},
                    "content": {"type": ["string", "null"]},
                    "create_time": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
        "conversation_summaries": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["conversation_no", "summary", "participants"],
                "properties": {
                    "conversation_no": {"type": "string", "minLength": 1},
                    "summary": {"type": "string"},
                    "participants": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": True,
            },
        },
        "current_profile": {"type": ["object", "null"]},
        "statistics": {
            "type": "object",
            "required": ["message_count_30d", "active_chats", "top_keywords"],
            "properties": {
                "message_count_30d": {"type": "integer", "minimum": 0},
                "active_chats": {"type": "integer", "minimum": 0},
                "top_keywords": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": True,
        },
    },
    "additionalProperties": True,
}

USER_PROFILE_ANALYSIS_OUTPUT_SCHEMA = {
    "type": "object",
    "required": [
        "userid",
        "profile_action",
        "summary",
        "facts_to_add",
        "facts_to_update",
        "facts_to_retire",
        "confidence",
    ],
    "properties": {
        "userid": {"type": "string", "minLength": 1},
        "profile_action": {
            "type": "string",
            "enum": ["create", "update", "no_change"],
        },
        "summary": {"type": "string"},
        "facts_to_add": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "fact_type",
                    "label",
                    "description",
                    "evidence_msgids",
                    "evidence_conversation_nos",
                    "confidence",
                ],
                "properties": {
                    "fact_type": {"type": "string", "minLength": 1},
                    "label": {"type": "string", "minLength": 1},
                    "description": {"type": "string", "minLength": 1},
                    "evidence_msgids": {"type": "array", "items": {"type": "string"}},
                    "evidence_conversation_nos": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "additionalProperties": True,
            },
        },
        "facts_to_update": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "fact_id",
                    "fact_type",
                    "label",
                    "description",
                    "evidence_msgids",
                    "confidence",
                ],
                "properties": {
                    "fact_id": {"type": "string", "minLength": 1},
                    "fact_type": {"type": "string", "minLength": 1},
                    "label": {"type": "string", "minLength": 1},
                    "description": {"type": "string", "minLength": 1},
                    "evidence_msgids": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "additionalProperties": True,
            },
        },
        "facts_to_retire": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["fact_id", "reason", "confidence"],
                "properties": {
                    "fact_id": {"type": "string", "minLength": 1},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "additionalProperties": True,
            },
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "additionalProperties": True,
}
