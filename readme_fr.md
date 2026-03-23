# Synchronisation d'horloge via Modbus TCp pour un automate

[English  version](./readme.md)

Cet outil met à jour l'horloge d'un PLC via **Modbus TCP** (code fonction 16). Il a été réalisé pour des automates Crouzet afin de recaler leur RTC interne automatiquement.  
l'outil écrit les 8 registres de l'horloge en une seule commande.


| Registre (décimal) | Correspondance               | Valeurs pos. |
|:------------------:|------------------------------|:------------:|
| 55                 | Secondes                     | 0..59        |
| 56                 | Minutes                      | 0..59        |
| 57                 | Heures                       | 0..23        |
| 58                 | Jour sem. (Lundi:0, Dim:6)   | 0..6         |
| 59                 | Jour du mois                 | 1..31        |
| 60                 | Mois                         | 1..12        |
| 61                 | Année (2 chiffres)           | 0..99        |
| 62                 | Fuseau horaire (en heure)    | -12..+12     |

> Le fuseau horaire est calculé depuis l'ordinateur hôte en incluant l'heure été / hiver.

Ce script utilise, au choix, l'heure du locale du **système hôte ** ou un **serveur de temps NTP** sans modifier l'horloge de l'hôte.  
Il peut optionnellement "déclencher" la mise à l'heure à la seconde nulle suivante pour minimiser le décalage entre les automates.

[![Buy me a coffee](./res/default-yellow.png)](https://www.buymeacoffee.com/ju9hJ8RqGk)

> ce script est proposé comme exemple. Il doit être testé par rapport à votre environnement réel. Sentez vous libre de réaliser toutes les adaptations nécessaires.

---
## Diagramme de flux - modes de fonctionnements
````mermaid
flowchart LR
    A[Référence de temps] -->|ntp ou local| B(Ce script .py)
    B -->|Modbus TCP| D[PLC 1]
    B -->|Modbus TCP| E[PLC 2]
    B -->|Modbus TCP| F[PLC 3]
````

## Fonctionnalités

- Congiguration par fichier YAML
- Liste d'automate par adresse IP
- Device ID personnalisé par automate -  L'ID 0 est possible
- ADresse du registre de base, les autres sont considérés contigus
- Fonctionnement en adresse base 1 ou 0 (si la documentation de votre PLC est en base 1 - adresse du type 40001, utilisez `address_base: 1`)
- 3 modes:
  - **debug**: Calcule et affiche les registres, **aucune écriture**
  - **test**:  lecture de l'automate, calcul, écriture, vérification pour **le premier automate actif** seulement
  - **normal**: écriture sur **tous les automates actifs** de la liste (vérification optionnelle).
- **Aucun changement de l'horloge locale**, NTP n'est utilisé qu'en référence.
- Fichier de log configurable (par defaut `./clock-sync.log`).

---

## Installation

### Pré-requis

- **Python 3.9+**
- [pymodbus](https://pymodbus.readthedocs.io/en/latest/)
- [pyyaml](https://pypi.org/project/PyYAML/)
- Accès au réseau (TCP 502 pour Modbus) et service NTP (UDP 123)

### Installation des dépendences

```bash
pip install pymodbus pyyaml
````

A lancer depuis une ligne de commande. Assurez vous d'avoir accès aux automates.
pas de droits admin nécessaires. Assurez vous que le user puisse accéder au réseau.

## Usage

Depuis une ligne de commande
````bash
python clock_sync.py --config config.yaml
````


## Calculs du temps

- Choix de la source de temps
    - system: horloge système de l'hôte du script (datetime.now().astimezone()).
    - ntp: Requête SNTP vers le serveur de votre choix. L'horloge de l'hôte n'est pas modifiée
- Si `align_to_next_second_zero=true`, la mise à jour aura lieu à la prochaine minute pleine. Sinon, la mise à jour est immédiate avec l'heure courante.
- Un décallage en secondes manuel peut être appliqué
- Le fuseau horaire est calculé par usage de l'heure locale du système, y compris l'heure d'été, heure d'hivers.
- Les 8 registres sont écrit d'un coup.

## Automatisation
Vous pouvez utiliser un CronJob ou kes taches planifiées de Windows.  
Une fréquence entre 1 et 7 jours devrait vous apporter la justesse temporelle souaitée.
