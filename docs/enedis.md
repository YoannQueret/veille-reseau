# Analyse du service Enedis

Analyse réalisée le 17 novembre 2026 à partir de la [page publique](https://www.enedis.fr/panne-et-interruption), de ses scripts JavaScript et de réponses obtenues directement auprès du service. Enedis décrit également le rôle de ce service sur sa page [Dépannage et urgences](https://www.enedis.fr/aide-contact/depannage-et-urgences).

## Parcours constaté

1. Le visiteur saisit une adresse, une commune ou un code postal. Une géolocalisation est également proposée par Enedis.
2. À partir de trois caractères, le JavaScript interroge `https://data.geopf.fr/geocodage/search/?q=…&maxResults=5&autoComplete=1` : suggestions IGN avec coordonnées, code INSEE, code postal, type de lieu, rue, arrondissement et commune.
3. Le formulaire transmet le résultat retenu à `/resultat-panne-interruption` par paramètres d’URL : `insee`, `type`, `adresse`, `CPVille`, `name`, `street`, `city`, `district`, `departement`, `lat`, `long`.
4. La page de résultat interroge `GET https://www.enedis.fr/panne-interruption-ajax` avec `insee`, `type`, `adresse`, `CPVille`, `name`, `district` et `city`.
5. Elle affiche les opérations et leur géométrie sur une carte, avec des blocs incidents, travaux, courant rétabli, absence de coupure et vigilance/crise.

L’adaptateur du dashboard utilise les mêmes sources depuis son serveur, ce qui évite de dépendre du CORS du site tiers ou d’un onglet ouvert. La géolocalisation et la carte ne sont pas nécessaires au suivi demandé.

## Réponse JSON constatée

- `noCoupure`, `streetNotFound`, `resultRue` et les indicateurs d’erreur sont souvent les chaînes `"true"` / `"false"`, et pas des booléens JSON.
- `resultMegacache.codeRetour` vaut `0` pour les réponses exploitées.
- `resultMegacache.listeCoupuresInfoReseau` contient les opérations de la commune : `idCoupure`, `incidentCoupure`, `dateCoupure`, `dateRealimentation`, `coupureClose`, `listeAdresses`, `codeInsee` et d’autres métadonnées.
- Les clés numériques `"0"`, `"1"`, etc. contiennent les résultats géographiques pour l’affichage : `status`, `dateRealim`, `locations` et, pour une recherche à la rue, `resultRue`.
- Les types constatés dans le JavaScript sont `Incident BT`, `Incident HTA`, `Travaux BT`, `Travaux HTA`, `Delestage`, `Coupure clôturée` et `Réalimentation HTA`. L’adaptateur accepte aussi l’orthographe `Délestage`.
- `listeCrises` fournit les messages de crise. Le site comporte aussi des messages contextuels et régionaux définis côté Drupal ; ils ne sont pas tous exposés comme des coupures individuelles dans le JSON.

## Le piège du périmètre

La consultation réelle du **12 rue de la Paix à Paris** a renvoyé `noCoupure="true"` tout en incluant dans `listeCoupuresInfoReseau` des travaux avenue Parmentier et rue Riquet. Interpréter la simple présence d’éléments dans cette liste comme une panne à l’adresse recherchée donnerait un faux positif.

Règles retenues :

- Pour une commune, utiliser les opérations de `listeCoupuresInfoReseau`, avec leur identifiant Enedis, et exclure les coupures clôturées.
- Pour une rue ou une adresse, respecter `noCoupure` et ne retenir que les éléments numériques marqués `resultRue="true"`. La liste globale de la commune n’est pas une preuve de coupure à l’adresse.
- Si aucun résultat de rue n’est confirmé et qu’Enedis ne déclare pas explicitement l’absence de coupure, le statut reste inconnu.
- Une crise est présentée comme une vigilance à statut inconnu : elle n’établit pas que le courant est coupé dans un bâtiment précis.

## Historisation

Chaque consultation est persistée, y compris les échecs. Les épisodes communaux sont regroupés par `idCoupure`. Les résultats de rue n’exposent pas d’identifiant individuel fiable : ils sont regroupés par code INSEE, rue et type d’opération, tant qu’ils sont observés sans interruption confirmée.

Les dates de première détection, dernière présence et fin sont des **observations du dashboard**. Elles ne constituent pas une mesure de la durée réelle d’une interruption. Une panne de courte durée survenant entre deux contrôles peut être manquée. Plusieurs pannes à la suite dans la même rue et de même type peuvent être regroupées si aucun contrôle n’observe leur séparation. Les dates de début Enedis, lorsqu’elles existent, sont conservées séparément. Une heure prévisionnelle de rétablissement dépassée ne suffit pas à clôturer un épisode.

Une réponse exploitable sans l’opération clôture l’épisode à la date du contrôle. Une erreur ne le clôture jamais. L’archivage ou la pause du suivi conserve les épisodes non clôturés, explicitement indiqués comme interrompus/incertains dans l’interface.

## Limites du service

Ce point d’accès est utilisé par un site public ; il ne s’agit pas d’une API contractuelle ou versionnée. Aucun engagement de disponibilité, de cadence autorisée ou de stabilité de schéma n’a été établi lors de cette analyse. Le rythme initial de 15 minutes est un choix de fonctionnement de l’application. Il faut adapter `enedis.py` et les fixtures si Enedis change le service, ajoute une protection ou modifie ses statuts.

Les données sont des signalements réseau, sans télémesure du compteur ni garantie sur l’alimentation d’un logement. La précision d’une adresse est celle de sa rue. L’application ne reproduit pas l’ensemble des bandeaux régionaux éditoriaux du site et conserve un lien vers la consultation officielle pour chaque lieu. Le service peut être indisponible ou ne pas couvrir un lieu. L’application n’affiche aucune absence de coupure lorsque son adaptateur ne peut pas interpréter la réponse.
