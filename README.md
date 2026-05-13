# Diplomová práca: Návrh fotovoltickej elektrárne pre rodinný dom

**Autor:** Bc. Erik Sklenka  
**Inštitúcia:** STU v Bratislave, FEI  
**Rok:** 2026  

## Popis projektu
Tento repozitár obsahuje výpočtové jadro diplomovej práce zameranej na návrh FVE. Skripty v jazyku Python slúžia na:
* Spracovanie a filtráciu IMS dát spotreby.
* Simuláciu energetických tokov v hybridnom systéme (Dom - BESS - Virtuálna batéria).
* Výpočet ekonomickej návratnosti v 25-ročnom cykle.

## Štruktúra súborov
* `1_hladanie_priebehu.py` až `6_bateria_komb.py` - Hlavné analytické moduly.
* `data/` - Vzorové dátové štruktúry pre simuláciu.

## Požiadavky
Knižnice: `pandas`, `numpy`, `plotly`.
