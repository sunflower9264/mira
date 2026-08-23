from __future__ import annotations

from app.schemas.decision import DecisionAnswer, DecisionGroup, DecisionOption


DECISION_NONE_OPTION = "以上都不是"
DECISION_NONE_DESCRIPTION = "我会补充其它答案或否定这些选项。"
DECISION_MIN_BUSINESS_OPTIONS = 2
DECISION_MAX_BUSINESS_OPTIONS = 3
DECISION_MIN_GROUPS = 1
DECISION_MAX_GROUPS = 3


def append_none_option(groups: list[DecisionGroup]) -> list[DecisionGroup]:
    """Return groups with Mira's fixed fallback option appended once."""

    normalized: list[DecisionGroup] = []
    for group in groups:
        if any(option.label == DECISION_NONE_OPTION for option in group.options):
            normalized.append(group)
            continue
        none_option = DecisionOption(
            label=DECISION_NONE_OPTION,
            description=DECISION_NONE_DESCRIPTION,
            recommended=False,
        )
        normalized.append(group.model_copy(update={"options": [*group.options, none_option]}))
    return normalized


def validate_decision_groups(groups: list[DecisionGroup]) -> str | None:
    if len(groups) < DECISION_MIN_GROUPS or len(groups) > DECISION_MAX_GROUPS:
        return "decision_request.groups 数量必须在 1-3 之间"
    seen_ids: set[str] = set()
    for group in groups:
        group_id = group.id.strip()
        if not group_id:
            return "decision_request.groups.id 不能为空"
        if group_id in seen_ids:
            return "decision_request.groups.id 必须互不相同"
        seen_ids.add(group_id)
        label = group.label.strip()
        if not label:
            return "decision_request.groups.label 不能为空"
        if len(label) > 200:
            return "decision_request.groups.label 长度超过 200"
        options = group.options or []
        if len(options) < DECISION_MIN_BUSINESS_OPTIONS or len(options) > DECISION_MAX_BUSINESS_OPTIONS:
            return "decision_request.groups.options 数量必须在 2-3 之间"
        recommended_count = sum(1 for option in options if option.recommended)
        if recommended_count != 1:
            return "decision_request.groups.options 必须且只能有一个 recommended=true"
        if not options[0].recommended:
            return "decision_request.groups.options 推荐项必须排在第一位"
        seen_options: set[str] = set()
        for option in options:
            normalized = option.label.strip()
            if not normalized:
                return "decision_request.groups.options.label 不能为空"
            if len(normalized) > 80:
                return "decision_request.groups.options.label 长度超过 80"
            if normalized == DECISION_NONE_OPTION:
                return "decision_request.groups.options 不要包含平台固定选项"
            if normalized in seen_options:
                return "decision_request.groups.options.label 必须互不相同"
            seen_options.add(normalized)
            description = option.description.strip()
            if not description:
                return "decision_request.groups.options.description 不能为空"
            if len(description) > 200:
                return "decision_request.groups.options.description 长度超过 200"
        if group.placeholder is not None and len(group.placeholder) > 200:
            return "decision_request.groups.placeholder 长度超过 200"
    return None


def validate_decision_answers(
    groups: list[DecisionGroup],
    answers: list[DecisionAnswer],
) -> str | None:
    by_id = {group.id: group for group in groups}
    if len(by_id) != len(groups):
        return "decision_request.groups.id 必须互不相同"
    seen_answers: set[str] = set()
    for answer in answers:
        group = by_id.get(answer.group_id)
        if group is None:
            return "选项不合法"
        if answer.group_id in seen_answers:
            return "选项不合法"
        seen_answers.add(answer.group_id)
        selected = answer.selected or []
        if len(selected) != len(set(selected)):
            return "选项不合法"
        if group.type == "single" and len(selected) != 1:
            return "回答不完整"
        if group.type == "multi" and len(selected) < 1:
            return "回答不完整"
        valid = {option.label for option in group.options}
        if any(option not in valid for option in selected):
            return "选项不合法"
        if group.type == "multi" and DECISION_NONE_OPTION in selected and len(selected) > 1:
            return "选项不合法"
    if seen_answers != set(by_id):
        return "回答不完整"
    return None
