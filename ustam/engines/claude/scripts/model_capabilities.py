"""Documented native Claude model/effort policy; no account or environment probing.

Sources checked 2026-10-07:
https://platform.claude.com/docs/en/models/overview
https://code.claude.com/docs/en/model-config#adjust-effort-level
"""
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
CHIEF_EFFORTS = EFFORTS[:-1]  # Persistent project settings do not accept max.
PINNED_MODELS = ('claude-opus-5-5', 'claude-sonnet-5-5')
HAIKU_MODEL = 'claude-haiku-4-5-20251001'
CATALOG_MODELS = (*PINNED_MODELS, HAIKU_MODEL, 'opus', 'sonnet', 'haiku')
MINIMUM_VERSIONS = {'claude-opus-5-5': (2, 1, 280), 'claude-sonnet-5-5': (2, 1, 284)}


def capabilities(model):
    supported = model in PINNED_MODELS
    status = 'documented' if supported else ('unsupported' if model in (HAIKU_MODEL, 'haiku', 'claude-haiku-4-5') else 'unresolved_alias' if model in ('opus', 'sonnet', 'fable') else 'unverified_model')
    return {'id': model, 'efforts': list(EFFORTS) if supported else [],
            'chief_efforts': list(CHIEF_EFFORTS) if supported else [],
            'effort_supported': supported, 'effort_status': status,
            'origin': 'documentation' if model in (*PINNED_MODELS, HAIKU_MODEL) else 'backend_alias',
            'minimum_cli_version': '.'.join(map(str, MINIMUM_VERSIONS[model])) if model in MINIMUM_VERSIONS else None}


def validate_selection(model, effort, *, chief=False):
    if not isinstance(model, str) or not isinstance(effort, str):
        raise ValueError('Claude model and effort must be text')
    record = capabilities(model)
    allowed = record['chief_efforts' if chief else 'efforts']
    if record['effort_supported']:
        if effort not in allowed:
            raise ValueError('Claude ' + ('chief' if chief else 'helper') + ' effort for ' + model + ' must be one of: ' + ', '.join(allowed))
    elif effort:
        raise ValueError('Claude effort is unsupported or unresolved for ' + model + '; select default effort (empty string) or an explicitly documented model. Existing choices were not changed.')
    return effort


def validate_cli_version(model, version):
    minimum = MINIMUM_VERSIONS.get(model)
    if minimum is not None and (version is None or tuple(version) < minimum):
        raise ValueError(model + ' requires Claude Code ' + '.'.join(map(str, minimum)) + '+; update the CLI or explicitly choose another model.')
