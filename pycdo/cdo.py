

import subprocess
import re
from .utils import execute, run, parse_cdo_operator_listing
from typing import Dict, Any, Tuple

# --- Dynamic operator attachment utilities ---
HELP_HEADERS_RE = re.compile(r'^(NAME|SYNOPSIS|DESCRIPTION|OPERATORS|PARAMETER|ENVIRONMENT)\s*$', re.MULTILINE)
PARAM_LINE_RE = re.compile(r'^\s*([a-zA-Z0-9_]+)\s+([A-Z]+)\s+(.*\S)\s*$')
SYNOPSIS_LINE_RE = re.compile(r'^\s*([a-zA-Z0-9_<>,\[\]]+)\s+(.+)$')
OPER_LINE_RE = re.compile(r'^\s*([a-z0-9_]+)\s{2,}(.+)$', re.IGNORECASE)

TYPE_MAP = {
    "STRING": "str",
    "BOOL": "bool",
    "BOOLEAN": "bool",
    "INT": "int",
    "INTEGER": "int",
    "FLOAT": "float",
    "DOUBLE": "float",
    "NUM": "float",
}

def _split_sections(help_text: str) -> Dict[str, str]:
    sections: Dict[str, str] = {}
    matches = list(HELP_HEADERS_RE.finditer(help_text))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i+1].start() if i + 1 < len(matches) else len(help_text)
        sections[m.group(1)] = help_text[start:end].strip('\n')
    return sections

def _parse_parameters_block(block: str):
    params: Dict[str, Tuple[str, str]] = {}
    for line in block.splitlines():
        m = PARAM_LINE_RE.match(line)
        if not m:
            continue
        name, raw_type, desc = m.groups()
        py_type = TYPE_MAP.get(raw_type.upper(), 'str')
        params[name] = (py_type, desc.strip())
    return params

def _parse_operators_section(block: str):
    result: Dict[str, Dict[str, Any]] = {}
    current = None
    for line in block.splitlines():
        if not line.strip():
            if current:
                current['long_lines'].append("")
            continue
        m = OPER_LINE_RE.match(line)
        if m:
            name, short = m.groups()
            current = {'name': name, 'short': short.strip(), 'long_lines': []}
            result[name] = current
        else:
            if current:
                ln = re.sub(r'^\s{0,8}', '', line.rstrip())
                current['long_lines'].append(ln)
    return result

def _parse_synopsis_block(block: str):
    specs = []
    for line in block.splitlines():
        line = line.rstrip()
        if not line or line.startswith('#'):
            continue
        m = SYNOPSIS_LINE_RE.match(line)
        if not m:
            continue
        op_and_params, rest = m.groups()
        optional_params = []
        for opt_block in re.findall(r'\[([^\]]+)\]', op_and_params):
            for item in opt_block.split(','):
                item = item.strip()
                if item:
                    optional_params.append(item)
        required_part = re.sub(r'\[[^\]]+\]', '', op_and_params)
        parts = [p for p in required_part.split(',') if p]
        op_name = parts[0]
        required_params = parts[1:]
        io_tokens = rest.split()
        if len(io_tokens) >= 2:
            in_tokens = io_tokens[:-1]
            out_tokens = io_tokens[-1:]
        else:
            in_tokens = io_tokens
            out_tokens = []
        specs.append({
            'op': op_name,
            'required_params': required_params,
            'optional_params': optional_params,
            'in_tokens': in_tokens,
            'out_tokens': out_tokens,
            'is_template': '<operator>' in op_name
        })
    return specs

def _expand_template_specs(specs, operator_names):
    expanded = []
    for spec in specs:
        if spec.get('is_template'):
            for op_name in operator_names:
                new_spec = {k: v for k, v in spec.items() if k != 'op'}
                new_spec['op'] = op_name
                new_spec['is_template'] = False
                expanded.append(new_spec)
        else:
            expanded.append(spec)
    return expanded

def _build_docstring(op_name, op_spec, param_meta, description_section, op_docs):
    summary = op_docs.get(op_name, {}).get('short') or f"CDO operator '{op_name}'"
    lines = [summary, ""]
    if description_section:
        first_para = description_section.split('\n\n')[0].replace('\n', ' ').strip()
        lines.append(first_para)
        lines.append("")
    lines.append('Parameters')
    lines.append('----------')
    for rp in op_spec['required_params']:
        t, desc = param_meta.get(rp, ('str', f'{rp} parameter.'))
        lines.append(f"{rp} : {t}")
        lines.append(f"    {desc}")
    for op in op_spec['optional_params']:
        t, desc = param_meta.get(op, ('bool', f'Optional flag {op}.'))
        default_note = ' (default: False)' if t == 'bool' else ''
        lines.append(f"{op} : {t}, optional{default_note}")
        lines.append(f"    {desc}")
    if op_spec['in_tokens']:
        lines.append('infiles : str or sequence of str')
        lines.append('    Input file(s); path or list of paths.')
    if op_spec['out_tokens']:
        lines.append('outfile : str')
        lines.append('    Output file path.')
    lines.append('run_kwargs : dict, optional')
    lines.append("    Extra kwargs forwarded to runner (e.g. options='-f nc4').")
    lines.append('')
    lines.append('Returns')
    lines.append('-------')
    lines.append('str')
    lines.append('    Captured CDO textual output (if any).')
    long_body = "\n".join(op_docs.get(op_name, {}).get('long_lines', [])).strip()
    if long_body:
        lines.append('')
        lines.append('Notes')
        lines.append('-----')
        lines.append(long_body)
    return "\n".join(lines)

def _create_operator_function(op_name, op_spec, param_meta, description_section, op_docs):
    required_params = op_spec['required_params']
    optional_params = op_spec['optional_params']
    args_list = ['self'] + required_params
    if op_spec['in_tokens']:
        args_list.append('infiles')
    if op_spec['out_tokens']:
        args_list.append('outfile')
    for opt in optional_params:
        ptype = param_meta.get(opt, ('bool', ''))[0]
        default = 'False' if ptype == 'bool' else 'None'
        args_list.append(f"{opt}={default}")
    args_list.append('**run_kwargs')
    body = ["op_params = []"]
    for rp in required_params:
        body.append(f"op_params.append(str({rp}))")
    for op in optional_params:
        ptype = param_meta.get(op, ('bool', ''))[0]
        if ptype == 'bool':
            body.append(f"if {op}: op_params.append('{op}')")
        else:
            body.append(f"if {op} is not None: op_params.append(str({op}))")
    body += [
        "param_str = ''",
        "if op_params: param_str = ',' + ','.join(op_params)",
        f"full_operator = '{op_name}' + param_str",
        "# Normalize infiles"
    ]
    if op_spec['in_tokens']:
        body += [
            "if isinstance(infiles, (str, bytes)):",
            "    input_list = [infiles]",
            "else:",
            "    input_list = list(infiles)"
        ]
    else:
        body.append("input_list = []")
    if op_spec['out_tokens']:
        body.append("result = run(operator=full_operator, options=run_kwargs.pop('options', None), inputs=input_list if input_list else None, output=outfile)")
    else:
        body.append("result = run(operator=full_operator, options=run_kwargs.pop('options', None), inputs=input_list if input_list else None)")
    body.append("return result")
    import textwrap
    func_src = f"def {op_name}({', '.join(args_list)}):\n" + textwrap.indent("\n".join(body), '    ')
    docstring_text = _build_docstring(op_name, op_spec, param_meta, description_section, op_docs)
    func_src += f"\n{op_name}.__doc__ = {docstring_text!r}\n"
    ns = {'run': run}
    exec(func_src, ns)
    return ns[op_name]

def _attach_dynamic_methods(help_text: str, cls):
    sections = _split_sections(help_text)
    param_meta = _parse_parameters_block(sections.get('PARAMETER', ''))
    op_docs = _parse_operators_section(sections.get('OPERATORS', ''))
    specs = _parse_synopsis_block(sections.get('SYNOPSIS', ''))
    if any(s.get('is_template') for s in specs):
        operator_names = list(op_docs.keys())
        if not operator_names:
            # fallback: parse NAME line list
            name_block = sections.get('NAME', '')
            name_line = next((ln for ln in name_block.splitlines() if ',' in ln and '-' in ln), '')
            if name_line:
                left = name_line.split('-', 1)[0]
                operator_names = [o.strip() for o in left.split(',') if o.strip()]
        specs = _expand_template_specs(specs, operator_names)
    for spec in specs:
        op_name = spec['op']
        if hasattr(cls, op_name):
            continue
        func = _create_operator_function(op_name, spec, param_meta, sections.get('DESCRIPTION', ''), op_docs)
        setattr(cls, op_name, func)

class Cdo:
    def __init__(self, cdo_path="cdo"):
        self.cdo_path = cdo_path
        self.operators = parse_cdo_operator_listing(run(options="--operators"))
        # Attach any multi-operator template help expansions (e.g., sinfo/sinfon)
        for op in self.operators['name']:
            self._attach_operator(op)

        
    def _attach_operator(self, operator):
        try:
            help_text = run(options='-h', operator=operator)
            _attach_dynamic_methods(help_text, self.__class__)
        except Exception:
            # best-effort; ignore if cdo or operator not available
            print(f"Warning: Could not attach operator '{operator}'.")


    def list_operators(self):
        return self.operators.name.tolist()