# Credits and academic references

falsegreen builds on published research in test smells and rotten green tests. The
work below shaped its concepts, its rule catalog, and the design of its two layers
(deterministic scanner plus an LLM semantic pass). Credit to the authors.

## Conceptual foundation

**A Multimethod Study of Test Smells: Cataloging, Removal, and New Types.**
Elvys Alves Soares ([@eas5](https://github.com/eas5),
[@elvyssoares](https://github.com/elvyssoares)). PhD thesis, Universidade Federal de
Pernambuco (UFPE), 2023. Supervisors: André Luís de Medeiros Santos, Márcio de
Medeiros Ribeiro.
The source for falsegreen's definition of a *rotten green test* (a passing test
that holds at least one assertion that never executes), the smell vs ineffective
vs rotten distinction, and the AAA Assert-phase framing.

**Rotten Green Tests.** Julien Delplanque, Stéphane Ducasse, Guillermo Polito,
Andrew P. Black, Anne Etien. ICSE 2019. The origin of the rotten-green-test concept
that falsegreen is named after.

## Detection catalog and tooling

**Test Smell Catalog** (semantic-logic section). easy-software-ufal.
<https://test-smell-catalog.readthedocs.io/> and
<https://github.com/easy-software-ufal/catalog-test-smells>. Cross-walked against
falsegreen's scope; the basis for the six-judgment semantic index.

**PyNose: A Test Smell Detector For Python.** Tongjie Wang
([@WANGJIEKE](https://github.com/WANGJIEKE)), Yaroslav Golubev
([@areyde](https://github.com/areyde)), Oleg Smirnov, Jiawei Li, Timofey Bryksin
([@jzuken](https://github.com/jzuken)), Iftekhar Ahmed. ASE 2021.
<https://github.com/JetBrains-Research/PyNose>. A Python (unittest) test smell
detector. falsegreen covers the effectiveness subset of PyNose's catalog and
deliberately excludes its maintainability and style smells; PyNose confirmed the
value of the planned unittest/xUnit dialect coverage.

## LLM and refactoring research

**Agentic LMs: Hunting Down Test Smells.** Rian Melo, Pedro Simões, Rohit Gheyi,
Marcelo d'Amorim, Márcio Ribeiro, Gustavo Soares
([@gustavoasoares](https://github.com/gustavoasoares)), Eduardo Almeida, Elvys
Soares ([@elvyssoares](https://github.com/elvyssoares)). SBES 2025. arXiv:2504.07277. Empirical evidence that small local models in
agent-based workflows detect and refactor test smells (Phi-4-14B, pass@5 of 75.3%;
six generated pull requests merged into open-source projects). Backs falsegreen's
LLM semantic pass and the AI-applies-the-fix path of the dual-use report.

**Beyond Green Tests: Removing Smells From Natural Language Tests.**
Manoel Aranda III, Márcio Ribeiro. Universidade Federal de Alagoas (UFAL), 2025.
doi:10.1145/3661167.3661225. Transformations and an NLP tool for natural-language
(manual) test smells. Out of scope for falsegreen (which targets pytest code), but
its *Unverified Action* smell is the manual-test analogue of an empty/neverfail
test, and it is a possible future direction if falsegreen ever covers BDD or
manual specs.

## A note on the GitHub handles

Handles above were matched by name and affiliation (a bio naming the same
institution as the paper, or membership in the tool's repository). Authors without
a confidently matched account are credited by name only, to avoid tagging the wrong
person. If you are one of these authors and want your handle added or removed, open
an issue.
