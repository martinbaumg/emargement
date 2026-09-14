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
que dans la fiche détaillée d'une séance, celle qui s'ouvre au survol de l'icône 📁
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

### Exclure des séances du PDF

Certaines séances n'ont pas besoin de signature, par exemple « Travail Autonomie ». Elles peuvent
être retirées du PDF sans disparaître de la liste des cours :

- **une séance** : interrupteur « Inclus / Exclu » sur chaque cours, enregistré immédiatement ;
- **toutes les séances d'un cours**, toutes semaines confondues : bouton « Toujours exclure ce
  cours » (exclusion par titre exact). Les cours concernés apparaissent sous forme de tags en bas
  de la page ; la croix d'un tag remet le cours sur le PDF.

Le PDF est recalculé en conséquence : total d'heures, regroupement des créneaux consécutifs. La
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

### Thème FIP

Dans « Profil », l'interrupteur « Je suis FIP » (désactivé par défaut) remplace tout le bleu du DSFR
par du rose `#F60975`. L'application sert alors une copie de `dsfr.min.css` où chaque bleu
(variables des thèmes clair et sombre, couleurs fixes, images SVG intégrées) devient un rose de
même clarté relative : le bleu principal `#000091` devient exactement `#F60975`, ses nuances plus
claires ou plus foncées des roses plus clairs ou plus foncés. Cette copie est générée à la volée
depuis le DSFR embarqué, donc suit ses mises à jour.

### Total des heures au choix

Dans « Profil », l'interrupteur « Remplir le total des heures de formation sur le PDF » permet de
laisser la case « Total heures de formation » vide, pour la remplir à la main. Il est désactivé par
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

La base SQLite est migrée automatiquement au démarrage : ajout des tables `lesson_exclusions` et
`title_exclusions`, et des colonnes `profiles.show_total_hours`, `profiles.show_teacher_names` et
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
