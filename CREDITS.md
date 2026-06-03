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

**Evaluating LLMs Effectiveness in Detecting and Correcting Test Smells: An
Empirical Study.** E. G. Santana Jr., Jander Pereira Santos Junior, Erlon P.
Almeida, Iftekhar Ahmed, Paulo Anselmo da Mota Silveira Neto, Eduardo Santana de
Almeida. 2025. arXiv:2506.07594. Found that LLM-driven correction sometimes
introduces new smells and reduces test coverage (only Gemini improved coverage).
Direct evidence behind falsegreen's validation gate for the AI-fix path: a proposed
fix must be checked, not trusted.

**Evaluating Large Language Models in Detecting Test Smells.** Keila Lucas, Rohit
Gheyi, Elvys Soares, Márcio Ribeiro, Ivan Machado. SBES 2024. arXiv:2407.19261.
LLMs detected 21 of 30 test smell types across seven languages (ChatGPT-4 best).
Backs falsegreen's choice to handle cross-language coverage in the language-agnostic
semantic pass rather than in the Python-only scanner.

**Test smells in LLM-Generated Unit Tests.** Wendkûuni C. Ouédraogo, Yinghua Li,
Xueqi Dang, Xunzhu Tang, Anil Koyuncu, Jacques Klein, David Lo, Tegawendé F.
Bissyandé. 2024. arXiv:2410.10628. Empirical evidence that LLM-generated tests carry
many smells, supporting falsegreen's premise that AI coding assistants are a
high-volume source of tests that need an effectiveness check.

**SENTINEL: Processo para Remoção Automática de Test Smells.** Adriano Pizzini.
PhD thesis, PUCPR / PPGIa, Curitiba, 2024. Advisor Andreia Malucelli, co-advisor
Sheila Reinehr. SENTINEL validates a test refactoring by cloning the project and
comparing the execution trace (branch flow and object state) before and after the
change. falsegreen adapts that idea as the validation gate for its AI-fix path:
after an LLM strengthens a flagged test, run it on a clean replica (must still pass)
and on a mutated replica (must now fail), paired with mutmut/cosmic-ray.

**Uma Investigação sobre Test Smells em Códigos de Testes JavaScript.**
Dalton Nicodemos Jorge ([@daltonjorge](https://github.com/daltonjorge)). PhD thesis,
UFCG, 2023. Advisors Patrícia D. L. Machado, Wilkerson L. Andrade. Tool STEEL:
<https://github.com/daltonjorge/steel>. Its JavaScript Exception Test smell (a
`try/catch` that swallows the thrown error) and assertion-in-`forEach`-over-empty
sharpened the skill's "Frontend cues by language" with two J1 cues for Jest/Vitest.

**Detecção de smells em testes automatizados em diferentes linguagens de
programação.** Gustavo Augusto Calazans Lopes. TCC, UFAL, 2023. Advisor Márcio de
Medeiros Ribeiro, co-advisor Elvys Alves Soares. Its srcML-per-language approach
(one shared rule backend over a common AST, plus a per-framework assert vocabulary)
validated falsegreen's decision to keep the deterministic scanner Python-only and
delegate cross-language coverage to the language-agnostic semantic pass, and it
informs how a future pluggable per-language frontend should be factored.

## A note on the GitHub handles

Handles above were matched by name and affiliation (a bio naming the same
institution as the paper, or membership in the tool's repository). Authors without
a confidently matched account are credited by name only, to avoid tagging the wrong
person. If you are one of these authors and want your handle added or removed, open
an issue.
