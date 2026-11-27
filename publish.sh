#!/usr/bin/env bash
# Filter the private source into ../cv, rebuild the PDF and the web page,
# compare the two PDFs, and refuse to finish if anything private leaked.
set -euo pipefail
cd "$(dirname "$0")"
python3 tools/make_public.py "$@"
