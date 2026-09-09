"""Atalho histórico: a trava passou a cobrir as duas bases da CVM.

A verificação de FII sozinha deixou passar uma base de fundamentos sem as
colunas de crédito. Toda a lógica está em `verificar_bases.py`; este arquivo
existe só para não quebrar quem chama pelo nome antigo.
"""

import sys

from verificar_bases import main

if __name__ == "__main__":
    sys.exit(main())
