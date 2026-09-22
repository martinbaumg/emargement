# emargement

Feuille d'émargement auto-hébergée pour le portail PASS d'IMT Atlantique (Alcuin/OpenPortal).
L'étudiant se connecte avec ses identifiants PASS, l'application récupère son emploi du temps
de la semaine et génère la feuille d'émargement PDF pré-remplie, à imprimer et à faire signer
à la main.

## Modifications

### Signature électronique retirée

Auparavant, chaque cours pouvait être signé dans l'application : l'étudiant dessinait
sa signature, et l'intervenant signait via un lien public `/sign/<token>` ou un QR code, sans
compte. La feuille doit de toute façon être signée à la main, donc tout ce circuit a été retiré :

- zone de signature étudiant et bouton « Signer les cours sélectionnés » ;
- page « Intervenants », liens de signature et QR codes (`/teachers`, `/sign/<token>`) ;
- absences marquées par les intervenants ;
- dépendance `qrcode`.

Dans le PDF, les colonnes « Signature intervenant » et « Signature étudiant » sont conservées mais
laissées vides. Le nom de l'intervenant reste pré-rempli depuis PASS (voir ci-dessous).

### Nom des intervenants

Les vues de l'agenda PASS (Tableau, Semaine…) n'indiquent pas les intervenants. Ils ne figurent
que dans la fiche détaillée d'une séance, celle qui s'ouvre au survol de l'icône en forme de dossier
(`Eplug/Agenda/Eve-Det.asp`). « Actualiser depuis PASS » charge donc cette fiche pour chaque
séance de la semaine et en retient la liste « Formateur(s) », affichée sur la page des cours et
dans la colonne « Nom intervenant » du PDF. La liste des apprenants, présente dans la même fiche,
n'est pas conservée.

Sur le PDF, les noms sont abrégés en « NOM P. » (« BAUMGAERTNER Martin » devient
« BAUMGAERTNER M. », « Pierre-Antoine » donne « P.-A. ») ; la page des cours affiche le nom
complet.

Dans « Profil », l'interrupteur « Remplir le nom des intervenants sur le PDF » permet de laisser la
colonne « Nom intervenant » vide. Il est activé par défaut ; les noms restent affichés sur la page
des cours dans tous les cas.

### Tickets d'assistance

Le menu « Assistance » (`/tickets`) permet à un étudiant connecté d'ouvrir un ticket : sujet,
adresse e-mail de réponse et message. Ses tickets restent consultables sur la page, avec leur
statut (Ouvert, En cours, Traité) et la réponse reçue.

L'administrateur — le compte dont l'identifiant PASS est dans `ADMIN_USERNAME` — voit en plus
« Tickets reçus » dans le menu, avec le nombre de tickets à traiter, et l'espace `/admin/tickets`
où il change le statut et rédige la réponse.

Les e-mails partent par SMTP, entièrement configuré par variables d'environnement :

| Variable | Rôle |
|---|---|
| `ADMIN_USERNAME` | identifiant PASS de l'administrateur ; vide = pas d'espace d'administration |
| `ADMIN_EMAIL` | adresse prévenue à l'ouverture d'un ticket (défaut `contact@baumgaertner.fr`) |
| `SMTP_HOST`, `SMTP_PORT` | serveur d'envoi (port 587 par défaut) |
| `SMTP_SSL` | `1` pour un port en TLS implicite (465) ; sinon STARTTLS |
| `SMTP_USER`, `SMTP_PASSWORD` | identifiants du compte d'envoi |
| `SMTP_FROM` | expéditeur (défaut : `SMTP_USER`) |

Sans configuration SMTP, le ticket est quand même enregistré et l'appli le dit clairement : rien
n'est perdu, l'administrateur le voit dans son espace.

Ces variables se mettent dans un fichier `.env` à la racine du projet, à côté de
`docker-compose.yml` : `cp .env.example .env`, puis remplissez-le. Docker Compose le lit tout seul,
et `run_local.py` aussi (une variable déjà définie dans le terminal reste prioritaire). Ce fichier
n'est jamais versionné ; seul le modèle `.env.example` l'est.

Exemple pour une boîte iCloud+ sur domaine personnalisé (le domaine est chez OVH, les MX pointent
vers iCloud, donc l'envoi passe par iCloud et non par OVH) :

```sh
SMTP_HOST=smtp.mail.me.com
SMTP_PORT=587
SMTP_USER=votre-identifiant@icloud.com     # l'identifiant Apple, pas l'alias du domaine
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx          # mot de passe pour application (appleid.apple.com)
SMTP_FROM=contact@baumgaertner.fr          # adresse déclarée dans iCloud Mail
```

Apple refuse le mot de passe habituel : il faut un mot de passe pour application, et l'adresse
d'expédition doit exister dans iCloud Mail. Pour vérifier une configuration sans lancer l'appli :

```sh
cd attendance_app && .venv/bin/python -m mailer vous@exemple.fr
```

### Assistant automatique

Un bouton « Assistant » flotte en bas à droite des pages. Il ouvre une modale DSFR où la
conversation se fait uniquement par choix : chaque réponse en propose de nouveaux, une pause de
« saisie » simule une réflexion, et toutes les branches finissent sur « Je sais pas, je m'en fous. »
Le dernier écran propose de reprendre au début ou d'ouvrir un vrai ticket. Les réponses sont
écrites dans `CHAT_TREE` (`attendance_app/layout.py`), rien n'est envoyé au serveur.

### Palmarès

Le menu « Palmarès » (`/badges`) transforme les semaines déjà chargées en quinze badges :
« Survivant du 8h » (cinq séances qui commencent à 8h00 ou avant), « Zéro vendredi » (une semaine
entière sans cours le vendredi), « Marathon 10h » (une journée de dix heures entre le premier et le
dernier cours), « Trou noir », « Grand chelem », « Tour du propriétaire », « Centurion »…

Un badge obtenu affiche sa ligne, un badge à décrocher affiche sa règle et sa progression
(`7 / 10`, `98h00 / 100h00`) ; les plus proches du but passent en tête. Le nombre de badges donne un
rang, de « Fantôme du bâtiment B » à « Légende de l'émargement ».

On y arrive par le menu, ou par « Voir mon palmarès » en haut du tableau de bord d'audience.

#### Rareté, position et classement par TAF

Chaque badge indique la part des participants qui l'ont décroché, avec un palier — Commun (60 % et
plus), Peu commun, Rare, Épique, Inédit (personne) — et le bandeau du haut donne votre position :
« Vos 9 badges classants vous placent 2ᵉ sur 15 participants, dans le top 13 % » (le percentile
n'est écrit qu'à partir de deux participants — seul, il n'y a rien à classer). En bas de page, un classement
par TAF (COUAD, OPE, NETCLOUD…) compare les moyennes de badges par participant, le vôtre étant mis
en évidence.

**Personne n'est nommé nulle part.** La page n'affiche que des pourcentages, des moyennes de groupe
et votre propre position ; ni nom, ni pseudonyme, ni liste de personnes, et jamais qui détient quel
badge. Il n'y a pas de seuil d'effectif : un pourcentage de rareté comme une moyenne de TAF ne dit
quelque chose de quelqu'un que si l'on sait qui est compté dedans, et cette composition n'est
publiée nulle part. Est participant un compte ayant ouvert le palmarès au moins une fois.
L'anonymat vaut entre étudiants : l'administrateur, lui, a la base.

Les badges qui mesurent l'usage de l'application plutôt que les semaines de cours
(`USAGE_BADGES` : « Explorateur de semaines », « Lanceur d'alerte ») sont exclus du score classant,
sans quoi le classement récompenserait le fait d'ouvrir le site.

Côté calcul, chaque visite du palmarès met à jour la ligne de l'étudiant dans `badge_scores`
(TAF, nombre de badges, score, identifiants des badges obtenus). Les statistiques se lisent ensuite
dans cette seule table : une écriture par visite, au lieu de réévaluer tous les comptes à chaque
affichage de la page.

Chaque badge obtenu porte un bouton « Afficher sur le profil » : le badge choisi apparaît en haut
de la page « Profil », avec son icône et sa ligne, et un lien pour en changer. **Un seul à la
fois** — en choisir un remplace le précédent, et « Retirer du profil » n'en laisse aucun. Le choix
est gardé dans `profiles.featured_badge` (colonne ajoutée automatiquement au démarrage) ; il n'est
pas enregistré avec le formulaire du profil, donc « Enregistrer » ne l'écrase pas. Le serveur
revérifie que le badge est bien obtenu, à la mise en avant comme à l'affichage : un badge qui
cesse de l'être (des séances remises sur le PDF défont « Grand autonome ») disparaît du profil au
lieu de mentir.

Tout est calculé dans `attendance_app/badges.py` à partir de ce que l'application a déjà en base
(`lessons_cache`, les exclusions, `events_cache_meta`, `tickets`) : **aucun appel à PASS**, aucune
donnée nouvelle. Un badge ne connaît donc que les semaines ouvertes au moins une fois sur la page
« Cours » — c'est écrit sur la page. Les salles sont reconnues à leur code PASS (`BR-B02-017A`) ;
les amphis appelés par leur nom ne sont pas comptés.

### Compteur de visiteurs et tableau de bord d'audience

Le pied de page affiche « *N* visiteurs cette semaine », dans la rangée de liens du bas
(`fr-footer__bottom`) plutôt que dans la colonne de droite du corps, où quatre liens finissaient
empilés contre le bord. Le lien ouvre `/kpi`, un tableau de bord
d'audience qui prend son sujet très au sérieux : chiffre d'affichage en tête, objectif trimestriel,
courbe des pages vues sur 30 jours, répartition par heure, pages les plus consultées, puis les
sections « Production et chaîne de valeur », « Qualité de service » et « Projection et création de
valeur » — taux de rebond, écart-type, droite des moindres carrés prolongée à cinq ans avec son R²,
valorisation à 1 000 € le visiteur, coût par visiteur (0,00 €) et réunions de pilotage évitées.

Les chiffres sont réels ; c'est leur mise en scène qui ne l'est pas. Ce qui est enregistré, à chaque
page servie (`attendance_app/analytics.py`, table `site_hits`) : l'heure, la page, la méthode, le
code de réponse, un booléen « robot », et un identifiant aléatoire tiré pour le navigateur et gardé
dans son propre cookie de session. **Ni adresse IP, ni User-Agent, ni identifiant PASS** : le
compteur sait combien de navigateurs sont passés, jamais qui. Les lignes de plus de 400 jours
(`analytics.RETENTION_DAYS`) sont supprimées au fil de l'eau, les robots sont comptés à part
puisqu'ils ne gardent pas de cookie, et les fichiers statiques, `/healthz` et la feuille de style
FIP ne comptent pas comme des visites. La page est lisible sans être connecté, puisque le lien du
pied de page l'est aussi.

Les deux graphiques sont dessinés à la main, sans bibliothèque : la courbe est un SVG étiré à la
largeur de la page (`preserveAspectRatio="none"`, trait maintenu à 2 px par `vector-effect`),
l'histogramme des heures est une simple rangée de `<div>`. Ils ne sont pas dans une
`fr-content-media` — cette classe est une boîte flex centrée en colonne, faite pour des images :
elle réduisait les graphiques à la largeur de leur contenu et collait les libellés d'axe les uns
aux autres. Les dates sont posées à l'abscisse exacte de leur point, les heures partagent les
24 colonnes de l'histogramme et sont écrites toutes les six. Les marques prennent le bleu du DSFR par
`currentColor`, donc elles deviennent roses avec le thème FIP et suivent le mode sombre sans une
ligne de plus. Le détail de la courbe est aussi donné sous forme de tableau, sous le graphique.

### Guide à la première connexion

À la première connexion, une fenêtre (modale DSFR, en plein écran sur téléphone) explique l'usage
en quatre étapes : compléter le profil avec « Remplir depuis PASS », **vérifier les codes UE**
proposés avant d'enregistrer, choisir les séances, puis télécharger et faire signer la feuille.
Elle ne s'ouvre d'elle-même qu'une fois ; le lien « Guide d'utilisation » du pied de page la
rouvre.

### Exclure des séances du PDF

Certaines séances n'ont pas besoin de signature, par exemple « Travail Autonomie ». Elles peuvent
être retirées du PDF sans disparaître de la liste des cours :

- **une séance** : interrupteur « Inclus / Exclu » sur chaque cours, enregistré immédiatement ;
- **toutes les séances d'un cours**, toutes semaines confondues : bouton « Toujours exclure ce
  cours » (exclusion par titre exact). Les cours concernés apparaissent sous forme de tags en bas
  de la page ; la croix d'un tag remet le cours sur le PDF.

Le PDF est recalculé en conséquence (regroupement des créneaux consécutifs) ; le « Total heures de
formation » reste celui de toute la semaine, séances exclues comprises. La
période « Semaine du … au … » ne change pas. Si toutes les séances de la semaine sont exclues, un
message l'indique au lieu de générer un PDF vide.

### Code UE plus tolérant

Dans « Profil », chaque ligne de la table des UE (`Nom = CODE`) remplit la case « CODE UE » des
cours dont le titre PASS contient ce nom :

- la comparaison ignore majuscules, accents, apostrophes et ponctuation (« L'objet » reconnaît
  « L’objet », « LV-Anglais » se lit « lv anglais »), ainsi que le pluriel (« Projet » reconnaît
  « PROJETS ») ;
- les mots peuvent être dans n'importe quel ordre, mais doivent tous être présents. Les petits
  mots (de, et, l', dans, son…) ne sont pas exigés ;
- un mot contenant un chiffre peut être collé à d'autres caractères : « Projet S9 » reconnaît
  « Projet A3S9 » ;
- « Langue(s) » ou « LV » reconnaît un nom de langue (« Anglais S9 B », « LV-Anglais-… »), mais
  « Anglais » ne reconnaît pas « Espagnol » ;
- une faute de frappe d'une lettre est tolérée sur les mots d'au moins 7 lettres ;
- plusieurs mots-clés peuvent désigner la même UE, séparés par `|` :
  `Langues | Anglais | LV = LCI310`. Seul le premier nom apparaît dans la table de référence du
  PDF.

Il n'y a pas de correspondance approximative : un cours sans correspondance exacte garde une case
vide plutôt qu'un code faux.

Sur la page des cours, chaque séance affiche son code UE sous le titre (« CCU »), ou « Sans code
UE » si la table n'en donne pas et que la séance est sur le PDF. Une ligne résume la semaine : total
d'heures programmées (toutes les séances, exclues comprises, comme sur le PDF), séances exclues et
séances sans code UE, avec un lien vers la table. Un clic sur « Sans code UE », ou sur un code déjà
affiché pour le corriger, permet de taper le code directement à sa place (Entrée pour valider, Échap
pour annuler). La table du profil est mise à jour : la ligne « Nom de l'UE = CODE » est ajoutée,
ou corrigée si elle ne concerne que ce cours ; si le code venait d'une ligne plus générale
(« Langues = LCI310 »), une ligne propre au cours est insérée avant elle, pour ne pas changer les
autres cours. Toutes les séances concernées affichent aussitôt leur code. Les deux compteurs se mettent à jour quand on
inclut ou exclut une séance.

Le bouton « Remplir depuis PASS », sous la table, la complète avec les codes que PASS connaît pour
les cours déjà chargés :

1. pour un cours de chaque intitulé, la fiche de séance donne le nom de l'UE (« Projets ») et
   parfois directement son code (« Organismes » `UETAF-CCU-B` → `CCU`) ;
2. sinon, le code est cherché dans « Consultation Fiches UE » : catalogues de la formation du
   profil et de TAF d'abord, puis tous les autres ; seul un résultat dont le nom correspond
   vraiment à l'UE est retenu (`FIP-TES310-BR - Transition Ecologique et Sociétale` → `TES310`).

Les lignes déjà présentes ont toujours la priorité. Quand PASS propose plusieurs codes, le premier
est ajouté et les autres sont notés en commentaire (`# …`, ignoré) ; une UE sans code trouvé est
ajoutée en commentaire « à compléter ». Rien n'est enregistré : la table revient pré-remplie, à
vérifier avant « Enregistrer ».

### Thème FIP

Dans « Profil », l'interrupteur « Je suis FIP » (désactivé par défaut) remplace tout le bleu du DSFR
par du rose `#F60975`. L'application sert alors une copie de `dsfr.min.css` où chaque bleu
(variables des thèmes clair et sombre, couleurs fixes, images SVG intégrées) devient un rose de
même clarté relative : le bleu principal `#000091` devient exactement `#F60975`, ses nuances plus
claires ou plus foncées des roses plus clairs ou plus foncés. Cette copie est générée à la volée
depuis le DSFR embarqué, donc suit ses mises à jour.

### Total des heures au choix

Dans « Profil », l'interrupteur « Remplir le total des heures de formation sur le PDF » permet de
laisser la case « Total heures de formation » vide, pour la remplir à la main. Rempli, ce total
compte toutes les heures programmées de la semaine, y compris les séances exclues du PDF (travail en
autonomie…). Il est désactivé par
défaut.

### Interface repensée pour le téléphone

- **Cours** : sur téléphone, chaque séance est une carte (horaire, interrupteur, titre,
  intervenant) au lieu d'un tableau plus large que l'écran. Le tableau est conservé à partir de
  la largeur tablette.
- **Semaine** : barre « ‹ Semaine du … › », avec un lien pour revenir à la semaine courante.
- **Actions** : « Télécharger la feuille d'émargement » devient le bouton principal, « Actualiser
  depuis PASS » est en dessous. Les onglets des jours sont raccourcis sur téléphone (« Lun. 07 »).
- **En-tête** : sans logo, titre et bouton menu alignés ; liens du menu conformes au DSFR ;
  « Se déconnecter » dans les accès rapides.
- **Connexion** : pas de majuscule ni de correction automatique sur l'identifiant.
- **Profil** : bouton « Réimporter depuis PASS », boutons pleine largeur, remplissage automatique
  du nom et du prénom.
- Le bloc « Détails » sous chaque cours a été retiré.

### Divers

- DSFR mis à jour de 1.15.2 à 1.15.3.
- Pied de page : lien vers le code source et adresse de contact.
- `build-and-push.sh` publie l'image sur `ghcr.io/martinbaumg/emargement`.

## Mise à jour d'une installation existante

La base SQLite est migrée automatiquement au démarrage : ajout des tables `lesson_exclusions`,
`title_exclusions`, `site_hits` (le compteur de visiteurs) et `badge_scores` (les statistiques du
palmarès), toutes vides au départ, et des colonnes
`profiles.show_total_hours`, `profiles.show_teacher_names`, `profiles.featured_badge` et
`lessons_cache.teachers_json`
(les intervenants apparaissent après le prochain « Actualiser depuis PASS »). Les anciennes tables de signature
(`signatures`, `student_signatures`, `teacher_links`, `lesson_absences`…) ne sont ni lues ni
supprimées : les signatures déjà enregistrées restent dans le fichier `attendance.db`.

## Contenu du dépôt

- `pass_schedule.py` — scraper et CLI autonome pour l'agenda PASS (connexion CAS/SAMLv2, lecture
  de l'agenda). Utilisable seul : `python3 pass_schedule.py --login <identifiant>`.
- `attendance_app/` — application web Flask construite dessus (stockage SQLite, export PDF).

## Lancer l'application

```sh
docker compose up --build
```

ou directement :

```sh
cd attendance_app
pip install -r requirements.txt
python3 run_local.py
```

## Licence

WTFPL — voir [LICENSE](LICENSE).
