"""Ship engineering reasoning guidance directly in agent task packets."""
from .contracts import MODES
from .storage import PROJECT_ROOT


def guide(mode: str) -> str:
    if mode not in MODES:
        raise ValueError('未知任务模式')
    docs = PROJECT_ROOT / 'docs'
    return ((docs / 'agent-guide.md').read_text(encoding='utf-8') + '\n\n' +
            (docs / 'playbooks' / (mode + '.md')).read_text(encoding='utf-8'))
