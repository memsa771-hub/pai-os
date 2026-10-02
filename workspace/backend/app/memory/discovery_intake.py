"""Record bounded owner statements about unanswered discovery questions."""
import re
from sqlalchemy import select
from app.models import EventRecord, Workspace
from app.counseling.discovery import DISCOVERY_KEY, DISCOVERY_FOCI, DISCOVERY_STATUSES
from app.memory.extractor import _normalize

# A status is an intentional response, not a keyword in an unrelated fact.
_INTENTS = {
    'UNKNOWN': r"(?:i(?: am|'m)? (?:not sure|unsure)(?: about .+)?|i (?:don't|do not) know .+|i haven't decided .+|(?:mera|meri|mere) .+ (?:pata nahi|pata nahin|maloom nahi))",
    'DECLINED': r"(?:i (?:would )?(?:prefer|rather) not to (?:share|discuss|answer|say) .+|i (?:don't|do not) want to (?:share|discuss|answer) .+|(?:please )?keep my .+ private|(?:mera|meri|mere) .+ (?:nahi batana|nahin batana|share nahi karna))",
    'DEFERRED': r"(?:(?:please )?(?:ask|remind) me (?:about .+ )?(?:later|another time)|let's (?:discuss|talk about|answer) .+ (?:later|another time)|i(?: would|'d)? (?:like|prefer) to (?:discuss|answer|share) .+ (?:later|another time))",
    'NOT_APPLICABLE': r"(?:(?:my )?.+ (?:is not applicable|doesn't apply|does not apply|relevant nahi|laagu nahi))",
}
_BARE = {
    'UNKNOWN': {'not sure', 'unsure', "i don't know", 'i do not know', "haven't decided", 'pata nahi', 'pata nahin'},
    'DECLINED': {'prefer not to say', "i'd rather not say", "i don't want to share", 'private', 'nahi batana', 'share nahi karna'},
    'DEFERRED': {'later', 'not now', 'another time', 'baad mein', 'baad me', 'abhi nahi', 'abhi nahin'},
    'NOT_APPLICABLE': {'not applicable', "doesn't apply", 'does not apply', 'relevant nahi', 'laagu nahi'},
}
_FOCUS_WORDS = {
    'current_level': r'\b(?:level|class|degree|education|taleem|parhai)\b',
    'current_direction': r'\b(?:direction|study|career|plan|future|parhai)\b',
    'motivation': r'\b(?:motivation|reason|why|wajah)\b',
    'academic_performance': r'\b(?:grades?|gpa|marks|performance|result)\b',
    'budget': r'\b(?:budget|money|cost|paise|paisa)\b',
    'interests': r'\b(?:interests?|pasand|dilchaspi)\b',
    'strengths': r'\b(?:strengths?|skills?|good at)\b',
    'practical_constraints': r'\b(?:constraints?|family|location|timing|limitations?)\b',
    'education_history': r'\b(?:education|qualifications?|school|college|degree|taleem)\b',
    'work_history': r'\b(?:work|job|experience|employment|internship)\b',
    'target_location': r'\b(?:country|location|destination|city)\b',
    'target_timing': r'\b(?:timing|intake|when|start date)\b',
}


def _intent_matches(text, status, focus, answering_question):
    text = _normalize(text).rstrip('.! ')
    # Do not extract commands out of quoted, hypothetical or contradictory prose.
    if any(char in text for char in ('?', '"', '“', '”', ';')) or re.search(r"\b(?:if|unless|but|agar|except)\b", text):
        return False
    if answering_question and text in _BARE[status]:
        return True
    if re.fullmatch(_INTENTS[status], text):
        return True
    # A compact named answer such as "budget later" has no intervening claim.
    topic = _FOCUS_WORDS[focus].replace(r'\b', '')
    suffix = {
        'UNKNOWN': r'(?:not sure|unknown|pata nahi|pata nahin)',
        'DECLINED': r'(?:nahi batana|share nahi karna)',
        'DEFERRED': r'(?:later|not now|baad mein|baad me|abhi nahi|abhi nahin)',
        'NOT_APPLICABLE': r'(?:not applicable|relevant nahi|laagu nahi)',
    }[status]
    return bool(re.fullmatch(r'(?:my |mera |meri |mere )?' + topic + r' (?:is )?' + suffix, text))


def _reopens_focus(text, focus, prior):
    if not isinstance(prior, dict) or prior.get('status') not in {'DECLINED', 'DEFERRED'}:
        return False
    # Name the exact topic; generic readiness cannot reopen every suppressed
    # question, and broad "education" cannot pick between two education foci.
    target = re.escape(focus.replace('_', ' '))
    text = _normalize(text).rstrip('.! ')
    return bool(re.fullmatch(
        r"(?:i(?: am|'m) ready to (?:discuss|talk about|share) (?:my )?" + target + r"(?: now)?"
        r"|let(?:'s| us) return to (?:my )?" + target + r")", text))


def _question_focus(db, workspace, source_event):
    question = (workspace.settings or {}).get('student_discovery_question') or {}
    if not isinstance(question, dict) or question.get('channel_target') != source_event.target:
        return None
    asked = db.get(EventRecord, question.get('source_event_id')) if question.get('source_event_id') else None
    from app.services.pai import PAI_AGENT_NAME
    if (asked is None or asked.network_id != workspace.id or asked.target != source_event.target
            or asked.source != f'openagents:{PAI_AGENT_NAME}' or asked.timestamp >= source_event.timestamp):
        return None
    intervening = db.execute(select(EventRecord.id).where(
        EventRecord.network_id == workspace.id, EventRecord.target == source_event.target,
        EventRecord.timestamp > asked.timestamp, EventRecord.timestamp < source_event.timestamp,
        EventRecord.source.in_([f'openagents:{PAI_AGENT_NAME}', f'human:{workspace.owner_user_id}']),
    ).limit(1)).first()
    return None if intervening else question.get('focus')


def consume_discovery_statuses(db, workspace_id, turn, items):
    if not isinstance(items, list):
        return
    workspace = db.get(Workspace, workspace_id)
    source_event = db.get(EventRecord, turn.user_event_id)
    if (workspace is None or source_event is None or source_event.network_id != workspace_id
            or not workspace.owner_user_id or source_event.source != f'human:{workspace.owner_user_id}'):
        return
    # Even direct callers must use durable evidence, never caller-supplied text.
    source_text = (source_event.payload or {}).get('content')
    if not isinstance(source_text, str):
        return
    question_focus = _question_focus(db, workspace, source_event)
    settings = dict(workspace.settings or {})
    current = settings.get(DISCOVERY_KEY)
    states = dict(current) if isinstance(current, dict) else {}
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        focus, status = item.get('focus'), item.get('status')
        if not isinstance(focus, str) or focus not in DISCOVERY_FOCI or not isinstance(status, str) or status not in DISCOVERY_STATUSES:
            continue
        evidence = item.get('evidence')
        quote = evidence.get('quote') if isinstance(evidence, dict) else None
        if not isinstance(quote, str) or not quote.strip():
            continue
        quote = _normalize(quote)
        reopening = status == 'UNKNOWN' and _reopens_focus(source_text, focus, states.get(focus))
        if quote not in _normalize(source_text) or not (reopening or _intent_matches(
                source_text, status, focus, focus == question_focus)):
            continue
        if focus != question_focus and not re.search(_FOCUS_WORDS[focus], quote):
            continue
        states[focus] = {'status': status, 'source_event_id': turn.user_event_id}
    if states != current and states:
        settings[DISCOVERY_KEY] = states
        workspace.settings = settings
