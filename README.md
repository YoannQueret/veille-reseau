# Veille Réseau

Tableau de bord indépendant pour surveiller plusieurs villes ou adresses à partir des signalements du service public [Panne et interruption d’Enedis](https://www.enedis.fr/panne-et-interruption).

## À quoi sert l’application ?

Veille Réseau centralise le suivi de plusieurs lieux : domicile, bureaux, résidences ou communes entières. Le tableau de bord consulte les informations publiées par Enedis à intervalles réguliers et conserve un historique permettant de retrouver les incidents et travaux observés.

Le navigateur sert à gérer les lieux et à consulter les résultats. Les vérifications sont réalisées par un service côté serveur : fermer la page ne coupe donc pas la surveillance. Le serveur doit rester allumé et connecté à Internet.

## Installer sur Debian 13 (Trixie)

Cette procédure part d’une Debian 13 avec accès à Internet et un compte autorisé à utiliser `sudo`. Si vous travaillez directement en tant que `root`, retirez `sudo` des commandes. L’application ne nécessite ni installation de Python sur l’hôte, ni clé API, ni abonnement Enedis.

### 1. Préparer Debian

```bash
cat /etc/os-release
sudo apt update
sudo apt install -y ca-certificates curl
```

Vérifiez que le système indique Debian 13 / `trixie`. Il doit pouvoir joindre `download.docker.com` pour l’installation, Docker Hub pour construire l’image, puis `data.geopf.fr` et `www.enedis.fr` pour les recherches et contrôles.

### 2. Installer Docker Engine et Docker Compose

La procédure ci-dessous utilise le dépôt APT officiel Docker, compatible avec Debian 13, et le plugin Compose. Référence : [documentation officielle Docker pour Debian](https://docs.docker.com/engine/install/debian/), consultée le 17 novembre 2026.

Sur une installation neuve, passez directement à l’ajout du dépôt. Si la machine possède déjà Docker, Podman ou un service utilisant `containerd`/`runc`, consultez la section sur les paquets en conflit de cette documentation avant de remplacer les paquets existants.

Ajouter la clé de signature :

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

Déclarer le dépôt pour Debian 13 et l’architecture de la machine :

```bash
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: trixie
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

Vérifier l’installation :

```bash
sudo docker run --rm hello-world
sudo docker version
sudo docker compose version
```

La commande Compose utilisée dans ce guide est **`docker compose`**, avec un espace. Les exemples conservent `sudo` ; il n’est pas nécessaire d’ajouter votre utilisateur au groupe `docker`.

### 3. Copier le projet sur le serveur

Sur Debian, créer le dossier qui accueillera les sources :

```bash
sudo mkdir -p /opt/veille-reseau
sudo chown "$(id -un):$(id -gn)" /opt/veille-reseau
```

Depuis l’ordinateur contenant le projet, copier les fichiers vers le serveur en remplaçant `utilisateur@serveur` par votre connexion SSH :

```bash
scp -r Dockerfile compose.yaml .dockerignore app.py enedis.py storage.py auth.py static README.md docs utilisateur@serveur:/opt/veille-reseau/
```

Exécuter cette commande depuis le dossier du projet. Si les sources sont déjà présentes sur Debian, vous pouvez simplement utiliser leur dossier actuel au lieu de `/opt/veille-reseau`.

### 4. Construire et lancer l’application

Sur le serveur, depuis le dossier des sources :

Le dossier `data/` doit exister avant le premier démarrage et être accessible à l’utilisateur du conteneur (UID/GID `10001`). Si vous utilisiez déjà la version avec un volume nommé `veille-data`, suivez d’abord la procédure de migration ci-dessous.

```bash
cd /opt/veille-reseau
sudo install -d -o 10001 -g 10001 -m 0700 data
sudo docker compose config --quiet
sudo docker compose up -d --build
sudo docker compose ps
```

La première construction télécharge l’image Python et peut prendre quelques minutes. Au premier démarrage, si `data/auth.json` n’existe pas, l’application crée le compte **`admin` / `password`**. Connectez-vous puis ouvrez **Mon compte** pour personnaliser l’identifiant et le mot de passe (8 caractères minimum). Le fichier contient un hash salé ; le mot de passe en clair n’est pas conservé. Un compte déjà configuré reste inchangé lors des mises à jour et des redémarrages.

`-d` lance le service en arrière-plan. Attendez que `docker compose ps` affiche le service `veille` en cours d’exécution et, après le contrôle de santé, `healthy`.

Vérifier que l’application répond :

```bash
curl --fail http://127.0.0.1:8000/api/health
```

Réponse attendue : `{"ok": true, "worker_alive": true}`. Ce contrôle valide le serveur et le service de surveillance ; la connexion à Enedis sera vérifiée lors du premier contrôle d’un lieu.

### 5. Ouvrir le tableau de bord et ajouter des lieux

Si le navigateur est sur la même machine, ouvrir **http://localhost:8000**.

Si Debian tourne sur un serveur distant, ouvrir un tunnel depuis votre ordinateur et garder cette session SSH ouverte :

```bash
ssh -N -L 8000:127.0.0.1:8000 utilisateur@serveur
```

Puis ouvrir **http://localhost:8000** sur votre ordinateur. Le port du conteneur est volontairement publié sur `127.0.0.1` : accéder directement à `http://adresse-du-serveur:8000` ne fonctionne pas avec la configuration fournie.

Dans le tableau de bord :

1. Se connecter avec **`admin` / `password`** lors d’une nouvelle installation, ou avec ses identifiants existants. Le bouton **Mon compte** permet de les modifier. Cliquer ensuite sur **Ajouter un lieu**.
2. Saisir une ville, une rue ou une adresse et sélectionner une suggestion IGN.
3. Donner éventuellement un nom au lieu et choisir la fréquence de contrôle.
4. Cliquer sur **Activer la surveillance**. Le premier contrôle démarre automatiquement.
5. Ajouter les autres lieux, puis consulter la vue d’ensemble, l’historique et le journal des contrôles.

Le service démarre sans adresse préremplie ni données fictives. Le dossier `data/` de l’hôte est monté dans `/data` du conteneur. La base est donc directement accessible sur Debian à l’emplacement **`/opt/veille-reseau/data/veille.sqlite3`** si vous avez suivi ce guide. Les lieux, les consultations et les épisodes restent présents après un redémarrage, une reconstruction ou la suppression du conteneur.

### 6. Administrer et mettre à jour l’application

Toutes les commandes suivantes s’exécutent depuis le dossier contenant `compose.yaml`.

```bash
# Consulter les journaux (Ctrl+C quitte l’affichage, sans arrêter l’application)
sudo docker compose logs -f --tail=100

# Arrêter temporairement, puis reprendre
sudo docker compose stop
sudo docker compose start

# Redémarrer le service
sudo docker compose restart

# Reconstruire après avoir remplacé les fichiers sources par une nouvelle version
sudo docker compose up -d --build
```

Docker démarre avec Debian et la politique `restart: unless-stopped` relance l’application après un redémarrage de la machine. Si vous l’avez arrêtée explicitement, relancez-la avec `sudo docker compose start`.

Pour actualiser également l’image Python de base lors d’une mise à jour :

```bash
sudo docker compose build --pull
sudo docker compose up -d
```

Sauvegardez la base avant une mise à jour importante, selon la procédure ci-dessous.

### Dépannage de l’installation

| Symptôme | Vérification ou action |
| --- | --- |
| `docker: 'compose' is not a docker command` | Installer `docker-compose-plugin` depuis le dépôt officiel configuré ci-dessus. |
| Impossible de joindre le démon Docker | Exécuter `sudo systemctl status docker`, puis `sudo systemctl start docker` si nécessaire. |
| Permission refusée sur le socket Docker | Conserver `sudo` devant les commandes Docker. |
| Le montage `data` n’existe pas | Depuis le dossier du projet, exécuter `sudo install -d -o 10001 -g 10001 -m 0700 data`. |
| SQLite indique que la base est en lecture seule ou impossible à ouvrir | Vérifier les permissions du dossier `data/` et de son contenu : ils doivent être accessibles à l’UID/GID `10001`, utilisé dans le conteneur. |
| Port 8000 déjà occupé sur Debian | Dans `compose.yaml`, remplacer `127.0.0.1:8000:8000` par `127.0.0.1:8080:8000`, puis relancer `sudo docker compose up -d`. Utiliser alors le port hôte 8080 pour l’accès et la destination du tunnel SSH. |
| Port 8000 occupé sur votre ordinateur | Utiliser `ssh -N -L 8080:127.0.0.1:8000 utilisateur@serveur`, puis ouvrir `http://localhost:8080`. |
| Le tableau de bord répond, mais un lieu reste inconnu | Consulter le **Journal des contrôles** : une indisponibilité Enedis ou IGN n’est pas une confirmation de panne électrique. |
| Le conteneur s’arrête ou devient `unhealthy` | Consulter `sudo docker compose logs --tail=100` et `sudo docker compose ps`. |
| Impossible de lire ou créer le compte au démarrage | Vérifier les permissions du dossier `data/` et du fichier `auth.json` pour l’UID `10001`. Un fichier invalide n’est jamais remplacé automatiquement par le compte initial ; utiliser la procédure de récupération ci-dessous. |
| Identifiant ou mot de passe oublié | Reconfigurer le compte avec la même commande, puis redémarrer le service ; les lieux et l’historique sont conservés. |
| Trop de tentatives de connexion | Attendre 15 minutes avant de réessayer ; les échecs répétés sont limités par adresse de connexion. |
| La connexion réussit, mais le navigateur revient immédiatement à la page de connexion | Si l’accès est en HTTP local, vérifier que `COOKIE_SECURE` vaut `0`. La valeur `1` nécessite HTTPS pour que le navigateur renvoie le cookie de session. |

## Accès et confidentialité

L’application protège le tableau de bord et ses API de gestion par un **compte administrateur unique**. Les adresses, l’historique, les détails et les exports ne sont accessibles qu’après connexion. Le contrôle de santé `/api/health`, la page de connexion et son état de configuration restent accessibles sans session et ne révèlent aucune donnée de suivi. Une exception explicite permet de partager l’état d’un seul lieu par une URL secrète de supervision, décrite ci-dessous. Les adresses sont stockées sur votre serveur et transmises à IGN/Enedis pour les consultations.

Le mot de passe est protégé avec [scrypt](https://docs.python.org/3.13/library/hashlib.html#hashlib.scrypt), un sel aléatoire et une comparaison en temps constant. Les sessions expirent après 8 heures, sont stockées en mémoire côté serveur et utilisent un cookie `HttpOnly` et `SameSite=Strict`. Elles sont révoquées à la déconnexion ou au redémarrage du service. Le bouton **Se déconnecter** se trouve en haut du tableau de bord ; la surveillance continue en arrière-plan. Les connexions échouées sont limitées à 5 par adresse de connexion sur 15 minutes, avec une limite globale et une limite de calculs simultanés. Derrière un reverse proxy, l’adresse du proxy est utilisée pour cette limitation ; les en-têtes de provenance fournis par le client ne sont pas pris en compte.

L’accès proposé utilise HTTP local ou un tunnel SSH. Pour un accès réseau partagé, terminer HTTPS sur un reverse proxy et activer le cookie sécurisé dans un fichier `.env` situé à côté de `compose.yaml` :

```bash
printf 'COOKIE_SECURE=1\n' > .env
sudo docker compose up -d
```

La valeur par défaut est `COOKIE_SECURE=0` pour l’accès local en HTTP. Ne l’activer qu’une fois HTTPS disponible. Aucun mot de passe ne doit être placé dans `compose.yaml`, une commande shell ou le dépôt Git.

### Modifier les identifiants depuis le tableau de bord

Après connexion, cliquer sur **Mon compte** en haut de l’écran :

1. Modifier l’identifiant si nécessaire.
2. Saisir le **mot de passe actuel** pour autoriser la modification.
3. Saisir un nouveau mot de passe d’au moins **8 caractères** et sa confirmation, ou laisser ces deux champs vides pour conserver le mot de passe actuel.
4. Cliquer sur **Enregistrer et se reconnecter**.

La modification est enregistrée immédiatement, sans redémarrage du conteneur. Toutes les sessions sont fermées : reconnectez-vous avec les nouveaux identifiants. Les lieux, l’historique et les URL de supervision sont conservés.

Le compte initial `admin` / `password` est créé uniquement lorsque `data/auth.json` est absent. Un compte personnalisé n’est jamais réinitialisé automatiquement et un fichier existant invalide empêche le démarrage au lieu d’être remplacé.

### Récupérer un compte oublié depuis le serveur

Avec un accès administrateur au serveur, il reste possible de remplacer le compte depuis le terminal :

```bash
sudo docker compose run --rm -it veille python auth.py configure
sudo docker compose restart veille
```

Cette commande demande un identifiant, un mot de passe d’au moins 8 caractères et sa confirmation, avec saisie masquée du mot de passe. Les données de surveillance ne sont pas modifiées. Le redémarrage applique ce changement effectué hors de l’interface et révoque les sessions existantes.

Pour appliquer une nouvelle version des sources, utiliser `sudo docker compose up -d --build`. Les identifiants déjà configurés seront conservés.

Le fichier `data/auth.json` appartient à l’UID `10001` et est créé avec les droits `0600`. Il contient uniquement l’identifiant, le sel et le hash. Les sessions n’y sont pas enregistrées. Les modifications effectuées par **Mon compte** sont prises en compte immédiatement ; une modification externe du fichier nécessite un redémarrage du service.

## API de supervision par lieu, sans connexion

Chaque lieu peut disposer d’une **URL secrète en lecture seule**, destinée à un outil de supervision externe. Aucune session, aucun cookie ni en-tête `Authorization` n’est nécessaire pour la consulter. La possession de l’URL donne accès uniquement à l’état du lieu associé. Aucun lien n’est activé automatiquement.

### Créer, copier ou révoquer une URL

1. Se connecter au tableau de bord.
2. Dans la vue d’ensemble, sous le statut d’un lieu, cliquer sur **URL de supervision**.
3. Cliquer sur **Créer l’URL de supervision**.
4. Copier l’URL JSON ou celle de la sonde HTTP, puis la configurer dans votre outil de supervision.
5. Le bouton **Tester l’état sans connexion** permet de vérifier la réponse sans envoyer les cookies du navigateur.

Le jeton aléatoire contient 256 bits d’entropie et ne dépend ni du code INSEE, ni du nom, ni de l’identifiant du lieu. L’URL est affichée uniquement lors de sa création ou de sa régénération ; seule son empreinte SHA-256 est conservée dans SQLite. **Copiez-la avant de fermer la fenêtre.** Si vous l’avez perdue, utilisez **Régénérer l’URL** et mettez à jour votre outil externe. Une régénération invalide l’ancienne URL ; **Révoquer l’URL** désactive l’accès. L’archivage du lieu révoque également son URL. Une pause conserve le lien et renvoie un état `paused`.

Une URL reste valide après un redémarrage du conteneur, une déconnexion ou un changement du mot de passe administrateur. La création, la régénération et la révocation nécessitent toujours la connexion administrateur. Une URL ne donne aucun accès aux autres lieux, à l’historique, aux exports ou aux actions de gestion.

### Consulter l’état JSON

```text
GET /api/public/status/<JETON_SECRET>
```

Exemple à adapter avec l’URL copiée depuis l’interface :

```bash
curl --silent --show-error 'https://votre-serveur.example/api/public/status/<JETON_SECRET>'
```

Exemple de réponse avec un incident :

```json
{
  "name": "Bureau",
  "status": "outage",
  "incidents": [
    {
      "description": "Incident BT",
      "estimated_restoration": "17/11/2026 20:00"
    }
  ]
}
```

La réponse contient uniquement le **nom**, le **statut** et la liste des **incidents ou travaux**. Chaque élément reprend la description Enedis (`Incident BT`, `Incident HTA`, `Travaux BT`, `Travaux HTA`, etc.) et la prévision de rétablissement lorsqu’elle existe. Plusieurs signalements peuvent concerner un même lieu : chacun garde sa propre description et sa propre prévision.

`name` correspond au nom personnalisé ou, à défaut, au libellé du lieu. `estimated_restoration` reprend la date et l’heure annoncées par Enedis, en heure de Paris, ou vaut `null` si elles ne sont pas fournies. Ce type de signalement ne décrit pas une cause technique précise et la prévision ne confirme pas le retour du courant.

| `status` | Interprétation |
| --- | --- |
| `ok` | Aucun incident ni travaux dans le dernier contrôle exploitable, encore récent ; `incidents` vaut `[]`. |
| `works` | Travaux signalés par Enedis, décrits dans `incidents`. |
| `outage` | Incident signalé par Enedis, décrit dans `incidents`. |
| `unknown` | Pas de contrôle, source indisponible, données anciennes ou service de contrôle arrêté ; `incidents` vaut `null`. |
| `paused` | Surveillance du lieu suspendue ; `incidents` vaut `null`. |

Les contrôles de fraîcheur restent appliqués en interne : des données de plus de **deux intervalles de contrôle plus 60 secondes** sont considérées comme trop anciennes. Un ancien signalement n’est donc pas présenté comme une situation actuelle lorsque le résultat est incertain. Les champs techniques de diagnostic ont été retirés de cette réponse JSON.

Pour une URL valide, ce mode renvoie HTTP **200**, même lorsqu’un incident est détecté : l’outil doit lire le JSON. Une URL inconnue, révoquée, remplacée ou associée à un lieu archivé renvoie **404**. La réponse ne contient ni l’historique ni le jeton.

### Utiliser une sonde HTTP simple

Ajouter `?mode=probe` à la même URL :

```bash
curl --fail --silent --show-error 'https://votre-serveur.example/api/public/status/<JETON_SECRET>?mode=probe'
```

Cette variante conserve le corps JSON, mais renvoie **200 uniquement pour `status=ok`**, et **503 pour `works`, `outage`, `unknown` ou `paused`**. Elle convient aux superviseurs qui contrôlent seulement le code HTTP ; `curl --fail` termine avec un code d’échec lorsque la sonde renvoie 503. Un mode autre que `json` ou `probe` renvoie 400.

Les consultations lisent uniquement les résultats déjà enregistrés : elles ne changent pas la fréquence de surveillance et ne déclenchent aucun appel à Enedis. Les réponses ne sont pas mises en cache. Aucun CORS n’est activé ; cette API est destinée aux appels entre serveurs.

### Réseau et conservation des liens

L’adresse du serveur dans l’URL doit être joignable depuis votre système de supervision. Le montage Docker fourni reste limité à `127.0.0.1` : utilisez un tunnel ou un reverse proxy HTTPS accessible par votre superviseur. Si un reverse proxy impose une connexion supplémentaire, autorisez explicitement les requêtes `GET` vers `/api/public/status/` sans cette connexion, en conservant la protection des autres routes.

L’URL joue le rôle de clé d’accès : conservez-la dans la configuration privée du superviseur. L’application masque ce chemin dans ses journaux et émet une politique `no-referrer` ; configurez également le reverse proxy pour ne pas journaliser ces URL secrètes. Les empreintes et activations des liens font partie de la base SQLite sauvegardée. Restaurer une ancienne sauvegarde restaure les liens actifs à cette date ; régénérez-les ou révoquez-les après restauration si nécessaire.

## Fonctionnalités

- Plusieurs communes, rues ou adresses, avec un nom facultatif et une fréquence propre à chaque lieu.
- Contrôles automatiques toutes les 15 minutes par défaut, réglables de 5 minutes à 24 heures ; bouton de vérification manuelle.
- États distincts : incident, travaux, aucune coupure signalée, inconnu et pause.
- Historique persistant : regroupement des détections répétées en épisodes, conservation des dates de première/dernière détection et de fin observée, dates Enedis lorsqu’elles sont disponibles.
- Journal des consultations réussies et des erreurs, export CSV complet des épisodes, filtres et graphique sur sept jours.
- Fiche détaillée de chaque incident ou travaux : référence Enedis lorsqu’elle est fournie, type de réseau (BT/HTA), état communiqué, rues concernées, début annoncé, prévision de rétablissement et nombre de foyers lorsqu’il est renseigné.
- Dans la vue d’ensemble, affichage direct des signalements non clôturés sous chaque lieu : rues concernées, début, rétablissement prévu, dernière présence et accès aux détails. En l’absence de signalement ouvert, le dernier épisode clôturé reste visible. Les lieux en pause ou de statut inconnu présentent ces informations comme une situation antérieure non confirmée.
- Historique des vérifications d’un épisode, avec les prévisions successives de rétablissement et les éventuelles indisponibilités de la source.
- Archivage d’un lieu sans effacer son historique, pause/reprise, reprise du planning après redémarrage.
- Affichage en heure de Paris, interface adaptée aux mobiles, aucune ressource distante nécessaire pour afficher le dashboard.

## Fonctionnement du connecteur

L’application réunit trois composants dans un seul conteneur : un serveur HTTP Python pour l’interface et les actions, un service de surveillance en arrière-plan et une base SQLite dans `/data/veille.sqlite3`. L’interface est écrite en HTML, CSS et JavaScript ; elle actualise les résultats affichés toutes les cinq secondes, indépendamment de la fréquence de consultation Enedis.

Lors de l’ajout d’un lieu, le géocodage IGN fournit son code INSEE, son type et ses coordonnées. Le serveur enregistre ces informations et programme les contrôles. À chaque échéance, il interroge Enedis, interprète la réponse selon le périmètre du lieu, enregistre la consultation et met à jour l’historique des épisodes.

Pour une commune, il suit les opérations de la commune. Pour une adresse, il filtre les résultats propres à la rue : la présence d’une panne ailleurs dans la ville ne déclenche pas une alerte pour cette adresse. La précision reste celle de la rue, sans mesure de l’alimentation du bâtiment.

L’analyse du code public Enedis et les requêtes réelles ont été effectuées le **17 novembre 2026**. Le détail et les limites sont dans [docs/enedis.md](docs/enedis.md).

Le serveur reproduit les appels de consultation du site : géocodage IGN puis requête JSON à Enedis. Il ne se contente pas de vérifier si la page web répond. L’adaptateur n’utilise ni navigateur automatisé ni données de consommation Linky.

Une erreur réseau, une réponse non reconnue, une rue introuvable ou un contexte de crise donne un statut **inconnu**, jamais « aucune panne ». Une erreur ne clôture pas les épisodes ouverts. Les erreurs successives multiplient l’intervalle par 2, 4, 8 puis 16 ; un contrôle réussi rétablit l’intervalle normal. Les appels sont séquentiels, espacés d’au moins une seconde ; le bouton manuel ignore les lieux vérifiés depuis moins d’une minute. Jusqu’à 100 lieux peuvent être enregistrés.

Les détections répétées d’une même opération sont regroupées en un épisode. Une réponse exploitable qui ne contient plus l’opération clôture cet épisode à l’heure du contrôle. Les dates de détection et de fin observée ne sont donc pas les dates exactes de coupure et de rétablissement : une panne brève entre deux contrôles peut être manquée. Les éventuelles dates fournies par Enedis sont conservées séparément. Le point d’accès public Enedis peut évoluer et nécessiter une adaptation du connecteur.

### Consulter les détails d’un incident

Depuis un lieu surveillé, **Voir les signalements** ouvre son historique. Cliquer sur **Voir les détails** pour afficher la fiche d’un épisode ; les signalements de la section **Derniers événements** ouvrent également cette fiche.

La fiche sépare les données Enedis des dates observées par le tableau de bord. Elle affiche les rues/secteurs concernés, la prévision de rétablissement et jusqu’aux 200 derniers contrôles réalisés pendant cet épisode. Les prévisions successives sont conservées : une heure annoncée dépassée ne suffit pas à confirmer le retour du courant. Un nombre de foyers nul ou absent dans la réponse est présenté comme **non renseigné**. La cause technique précise n’est pas fournie dans les réponses exploitées et n’est donc pas déduite du type de panne.

Une mise à jour ajoute automatiquement le champ de stockage des détails aux bases existantes, sans supprimer leurs lieux ni leur historique. Les anciens épisodes conservent les informations déjà disponibles ; les épisodes encore observés seront enrichis au prochain contrôle réussi. Après remplacement des sources, reconstruire l’image avec `sudo docker compose up -d --build` pour charger la nouvelle version.

## Sauvegarde et restauration

### Emplacement de la base sur l’hôte

Le montage déclaré dans `compose.yaml` expose tout le répertoire de données :

| Sur Debian, depuis le dossier du projet | Dans le conteneur |
| --- | --- |
| `./data/veille.sqlite3` | `/data/veille.sqlite3` |
| `./data/veille.sqlite3-wal` et `./data/veille.sqlite3-shm`, lorsqu’ils existent | Fichiers annexes SQLite en mode WAL |
| `./data/auth.json` | Configuration du compte administrateur dans `/data/auth.json` |

Vous pouvez intégrer les sauvegardes aux outils de votre serveur sans passer par le conteneur. Le dossier est réservé à l’UID/GID `10001` ; utilisez `sudo` pour l’administrer. `data/` et `backups/` sont exclus de Git et du contexte de construction Docker.

Pour utiliser un autre emplacement, remplacez `source: ./data` dans `compose.yaml` par un chemin absolu sur Debian, créez ce dossier avec les mêmes permissions, puis recréez le conteneur avec `sudo docker compose up -d`. Déplacez les données existantes à l’arrêt avant de changer de chemin.

### Sauvegarder depuis Debian, application en fonctionnement

SQLite utilise le mode WAL : **ne copiez pas uniquement `veille.sqlite3` pendant que l’application écrit dedans**, car des transactions récentes peuvent se trouver dans le fichier `-wal`. La commande `.backup` produit un fichier autonome et cohérent, même pendant la surveillance.

Depuis le dossier du projet :

```bash
sudo apt install -y sqlite3
sudo install -d -m 0700 backups
backup_path="backups/veille-$(date +%Y%m%d-%H%M%S).sqlite3"
sudo sqlite3 -readonly data/veille.sqlite3 ".backup '$backup_path'"
sudo sqlite3 -readonly "$backup_path" 'PRAGMA integrity_check;'
```

Le contrôle doit renvoyer `ok`. Le fichier horodaté dans `backups/` peut ensuite être copié vers votre destination de sauvegarde habituelle. La même commande peut être exécutée par une tâche planifiée de l’hôte avec les droits nécessaires et des chemins absolus. Cette procédure n’utilise ni `docker compose exec` ni `docker compose cp`.

La sauvegarde SQLite ne contient pas le compte administrateur. Conservez également `data/auth.json` dans une sauvegarde privée, ou recréez le compte avec `auth.py configure` sur le serveur de restauration. Sans fichier de compte restauré ou reconfiguré, le prochain démarrage créera le compte initial `admin` / `password`. Par exemple, depuis le dossier du projet :

```bash
sudo install -m 0600 data/auth.json backups/auth.json
```

Pour restaurer ce compte, recopier le fichier avec `sudo install -o 10001 -g 10001 -m 0600 backups/auth.json data/auth.json`, puis redémarrer le service.

### Restaurer une sauvegarde

Cette opération remplace la base courante. Remplacez le nom du fichier ci-dessous par celui de votre sauvegarde, vérifiez que le contrôle renvoie `ok`, puis effectuez la restauration depuis le dossier du projet :

```bash
backup_path="backups/veille-20261117-150000.sqlite3"
sudo sqlite3 -readonly "$backup_path" 'PRAGMA integrity_check;'
sudo docker compose stop veille
sudo install -o 10001 -g 10001 -m 0600 "$backup_path" data/veille.sqlite3.restore
sudo rm -f data/veille.sqlite3-wal data/veille.sqlite3-shm
sudo mv data/veille.sqlite3.restore data/veille.sqlite3
sudo docker compose up -d
curl --fail http://127.0.0.1:8000/api/health
```

Conservez une sauvegarde de la base actuelle avant son remplacement. Les fichiers WAL/SHM sont supprimés uniquement après l’arrêt du service pour ne pas réutiliser ceux de l’ancienne base. Attendez le démarrage du service avant le contrôle HTTP.

### Migrer l’ancien volume nommé vers `data/`

Si vous avez déjà démarré une version utilisant `veille-data`, récupérez ses données **avant** de recréer le conteneur avec le nouveau montage. Depuis le même dossier de projet Compose, avec l’ancien conteneur encore présent :

```bash
sudo docker compose stop veille
sudo install -d -o 10001 -g 10001 -m 0700 data
sudo docker compose cp veille:/data/. ./data/
sudo chown -R 10001:10001 data
sudo docker compose up -d --build
```

Cette copie inclut les éventuels fichiers WAL/SHM et s’effectue à l’arrêt. Vérifiez ensuite vos lieux et votre historique dans le tableau de bord. Si l’ancien conteneur a déjà été supprimé, les données restent dans l’ancien volume Docker : récupérez-les avant de le supprimer. Ne supprimez pas ce volume avant d’avoir validé la migration et réalisé une sauvegarde.

Avec le montage de dossier actuel, `docker compose down`, y compris avec `-v`, ne supprime pas `./data/`. Sa suppression manuelle efface les données de l’application.

## Développement et tests

Python **3.11+** et les données de fuseaux horaires système suffisent. Sans Docker, le même compte initial est créé automatiquement si aucun compte n’existe :

```bash
python3 app.py
```

Variables disponibles : `HOST` (défaut `127.0.0.1`), `PORT` (défaut `8000`), `DATABASE_PATH` (défaut `data/veille.sqlite3`), `AUTH_FILE` (défaut : `auth.json` dans le même dossier que la base) et `COOKIE_SECURE` (`0` ou `1`). Le conteneur utilise `/data/veille.sqlite3` et `/data/auth.json`. Aucun mode de désactivation globale de l’authentification n’est prévu ; les liens de supervision constituent un accès limité et explicite par lieu.

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
```

Les tests sont autonomes, sans appel réseau externe. Les fixtures reprennent des réponses publiques Enedis observées, sans la géométrie volumineuse : une commune avec incidents, une rue avec travaux et une adresse sans coupure malgré des travaux ailleurs dans Paris. Les tests HTTP ouvrent uniquement des sockets de boucle locale.

Structure : `app.py` (HTTP et worker), `auth.py` (compte et sessions), `enedis.py` (géocodage/adaptateur), `storage.py` (SQLite), `static/` (interface et connexion), `tests/` (régressions).
