# Evidence grading rubric for CO2 capture materials

Draft for the domain expert to review. It has no numeric thresholds yet: those are his to set.

Label each claim with a **basis** and a **grade**. Both are your reading of the source. Code does not check them, and the app shows them as labels, not as verified facts.

## Basis

| Basis | When to use it |
| --- | --- |
| measured | The source reports an experiment on a real sample: an isotherm, a breakthrough curve, cycling |
| simulated | The number comes from simulation or a model (GCMC, DFT, machine-learning prediction, process modelling) |
| review | The source is a review or a perspective that reports others' results |
| unclear | The text you were given does not say |

## Grade

| Grade | When to use it |
| --- | --- |
| strong | Measured, under realistic gas conditions (humid or mixed gas at a CO2 partial pressure relevant to the application), with cycling reported, and compared with at least one other material |
| moderate | Measured, but only in dry single-component gas, or without cycling, or without a comparison |
| weak | Simulated only, a single idealised condition, a review's summary of others' work, or a source that calls its own result preliminary |

Downgrade one level when:

- the source is an abstract only, so the conditions cannot be read;
- a ranking of materials rests on simulation that the source does not compare with measured data.

When two sources disagree, prefer the one measured under conditions closer to the question (humid before dry, cycled before single-pass), then the one that compares more materials.
