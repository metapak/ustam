"""Antigravity project engine constants. Invoked only by the trusted Ustam worker."""
MODELS = ('inherit', 'flash', 'pro')
ROLES = ('acceptance-test-author', 'fast-lookup', 'explorer', 'researcher', 'implementer', 'verifier', 'reviewer', 'failure-analyst', 'qa-operator', 'advisor')
PROFILES = ('balanced', 'quality', 'economy', 'quota-saver', 'custom')
PRESETS = {p: {'owner': 'pro' if p in ('balanced', 'quality') else 'flash', 'helper': 'pro' if p == 'quality' else 'flash'} for p in PROFILES if p != 'custom'}
