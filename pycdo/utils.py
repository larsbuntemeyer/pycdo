import subprocess
import re
import pandas as pd

# Precompile a regex:
# ^(\S+)        -> operator name (non-space)
# (?:\s+(.*?))? -> optional description (non-greedy)
# \s*\(([-\d]+)\|([-\d]+)\)\s*$ -> final (in|out) with optional leading spaces
LINE_RE = re.compile(r'^(\S+)(?:\s+(.*?))?\s*\(([-\d]+)\|([-\d]+)\)\s*$')

ALIAS_RE = re.compile(r'-->\s*(\S+)')  # to detect alias target inside description

def execute(command, return_type="stdout", decode=True, verbose=False):
    if verbose is True:
        print(f"executing: {' '.join(command)}")
    output = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if output.returncode != 0:
        raise Exception(output.stderr.decode("utf-8"))
    if return_type == "output":
        return output
    if return_type == "stdout":
        return output.stdout.decode("utf-8")


def run(operator=None, inputs=None, output=None, options=None, verbose=False):
    if options is None:
        options = []
    elif isinstance(options, str):
        options = [options]
    if operator is None:
        operator = []
    elif isinstance(operator, str):
        operator = [operator]
    if inputs is None:
        inputs = []
    elif isinstance(inputs, str):
        inputs = [inputs]
    if output is None:
        output = []
    elif isinstance(output, str):
        output = [output]

    command = ["cdo"] + options + operator + inputs + output
    return execute(command, verbose=verbose)


def parse_cdo_operator_listing(text: str) -> pd.DataFrame:
    rows = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        m = LINE_RE.match(line)
        if not m:
            # If a line doesn't match, you can log or skip it
            # Here we skip but record the raw content if needed
            # print(f"Skip unparsable line: {line!r}")
            continue
        name, desc, n_in, n_out = m.groups()
        desc = (desc or '').rstrip()
        alias_match = ALIAS_RE.search(desc)
        is_alias = alias_match is not None
        alias_to = alias_match.group(1) if is_alias else None

        rows.append({
            "name": name,
            "description": desc if desc else None,
            "inputs": int(n_in),
            "outputs": int(n_out),
            "is_alias": is_alias,
            "alias_to": alias_to
        })
    df = pd.DataFrame(rows)
    # Optional: sort by name
    df = df.sort_values("name").reset_index(drop=True)
    return df

def parse_operator_help():
    pass
