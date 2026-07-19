# Architecture Oil Kam

Oil Kam est une application web interne pour station-service. La version actuelle vise une démonstration professionnelle : elle reste simple techniquement, mais couvre les principaux modules du cahier des charges.

## Principes

- Application monolithique volontaire : facile à comprendre, modifier et présenter.
- Serveur Python standard avec `http.server`.
- Base SQLite locale pour éviter une installation complexe.
- Interface HTML générée côté serveur, avec CSS/JS simples.
- Aucune dépendance externe obligatoire.

## Dossiers

- `app/database.py` : schéma, migrations simples, données de démonstration et fonctions métier testables.
- `app/server.py` : routes HTTP, permissions, vues HTML et actions utilisateur.
- `app/static/styles.css` : design responsive, cartes, tableaux, formulaires et impression.
- `app/static/app.js` : navigation active et enregistrement du service worker.
- `app/static/manifest.json` : métadonnées PWA.
- `app/static/service-worker.js` : cache PWA simple.
- `app/static/icons/` : icônes PWA simples.
- `data/` : base SQLite générée localement.
- `tests/` : tests unitaires.

## Tables principales

- `users` : comptes, email, mot de passe hashé, rôle, statut.
- `tasks` : définition des tâches, responsable, fréquence, heure limite, statut actif.
- `task_completions` : validations de tâches par utilisateur et date.
- `attendance_records` : pointages d'arrivée et de départ par utilisateur, date et heure.
- `products` : catalogue produits pour pertes, catégorie, prix unitaire, unité, statut.
- `losses` : déclarations de pertes, quantité, motif, date, déclarant, valeur calculée.
- `training_modules`, `training_quizzes`, `training_progress`, `training_certificates` : anciennes tables conservées comme base technique, mais le parcours Formation actuel utilise un guide opérationnel codé simplement dans `app/server.py`.

## Permissions

- Employé : tâches personnelles, pointage, déclaration de pertes, guide formation.
- Manager : tâches, historique, pointages, pertes globales, rapports, guide formation.
- Admin : toutes les fonctions manager + gestion utilisateurs, produits et consultation du guide formation.

## Exports et rapports

- Export CSV pour les pertes.
- Pages imprimables pour rapports.
- Export `.xlsx` non ajouté pour garder le projet sans dépendances externes.

## Évolutions possibles

- Remplacer les exemples de formation par les procédures définitives du tuteur.
- Ajouter une gestion admin des catégories et check-lists si le besoin est confirmé.
- Export Excel/PDF avec librairies dédiées.
- Journal d'audit.
- Pointage par période et export dédié.
- Sessions persistantes.
- Déploiement sur serveur interne.
