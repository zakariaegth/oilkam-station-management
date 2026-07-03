# Oil Kam - Application de gestion station-service

Application web de démonstration pour une station-service Oil Kam. Elle couvre les besoins principaux du cahier des charges : tâches quotidiennes, pertes de marchandises, formations internes, rapports et gestion des rôles.

La version actuelle est une base fonctionnelle et présentable : elle privilégie la stabilité, la simplicité et la lisibilité du code. Elle utilise Python standard et SQLite, sans framework ni dépendance externe.

## Fonctionnalités disponibles

### Authentification et rôles

- Connexion par email et mot de passe.
- Trois rôles : employé, manager, administrateur.
- Redirection vers un tableau de bord adapté au rôle.
- Protection des pages admin.
- Message clair en cas d'accès non autorisé.

### Comptes de démonstration

| Rôle | Email | Mot de passe |
| --- | --- | --- |
| Employé | employe@oilkam.demo | oilkam123 |
| Manager | manager@oilkam.demo | oilkam123 |
| Admin | admin@oilkam.demo | oilkam123 |

### Module tâches

- Liste des tâches du jour.
- Validation d'une tâche avec commentaire.
- Statuts : à faire, complétée, en retard.
- Création de tâches par manager/admin.
- Modification ou désactivation des tâches par manager/admin.
- Historique filtrable par date et employé.
- Statistiques d'avancement dans le tableau de bord manager.

### Module pertes

- Déclaration d'une perte par un employé.
- Produit, quantité, motif, date, commentaire.
- Calcul automatique : quantité x prix unitaire.
- Tableau des pertes déclarées.
- Filtres par date, produit et motif.
- Statistiques jour, semaine et mois.
- Consultation globale par manager/admin.
- Export CSV compatible Excel.

### Gestion admin des produits

- Liste des produits.
- Création et modification de produit.
- Désactivation via statut actif/inactif.
- Champs : nom, catégorie, prix unitaire, unité, statut.
- Les produits actifs alimentent le formulaire de pertes.

### Gestion admin des utilisateurs

- Liste des utilisateurs.
- Création d'utilisateur.
- Modification du nom, email, rôle et statut.
- Désactivation/réactivation.
- Réinitialisation du mot de passe.
- Protection contre la désactivation ou rétrogradation du dernier admin actif.

### Module formation

- Liste des formations disponibles.
- Contenu texte simple.
- Quiz de validation.
- Calcul automatique du score.
- Validation si le score atteint le minimum configuré.
- Suivi de progression par employé.
- Vue manager/admin de la progression équipe.
- Gestion admin des modules et quiz.

### Attestation

- Attestation HTML imprimable après validation d'une formation.
- Nom de l'employé, formation, date, score.
- Bouton "Imprimer / Enregistrer en PDF" via le navigateur.

### Rapports

- Rapport imprimable des pertes par période.
- Rapport imprimable des tâches complétées.
- Rapport imprimable de progression des formations.
- Bouton d'impression navigateur.
- Export CSV conservé pour les pertes.

## Lancer l'application

Prérequis : Python 3.11 ou plus récent.

```powershell
cd "D:\Documents\stage oil kam"
python -m app.server
```

Adresse locale :

```text
http://127.0.0.1:8000
```

La base SQLite est créée automatiquement dans `data/oilkam.db`.

## Mise en ligne / Démonstration à distance

### Solution recommandée pour une démonstration rapide

Pour une réunion ou un test court avec un tuteur, la solution la plus simple est un tunnel temporaire avec **cloudflared** ou **ngrok**.

Cette méthode garde l'application sur votre PC et crée une adresse HTTPS publique temporaire qui redirige vers `http://127.0.0.1:8000`.

Conséquences importantes :

- le PC doit rester allumé ;
- le serveur Python doit rester lancé ;
- la commande du tunnel doit rester ouverte ;
- le lien est temporaire ;
- il faut utiliser uniquement les comptes de démonstration, sans données réelles.

### Option 1 - Cloudflare Tunnel

Terminal 1 :

```powershell
cd "D:\Documents\stage oil kam"
python -m app.server
```

Terminal 2 :

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

Cloudflared affiche une adresse du type :

```text
https://exemple.trycloudflare.com
```

C'est ce lien HTTPS qu'il faut envoyer au tuteur avec les comptes de démonstration.

### Option 2 - ngrok

Terminal 1 :

```powershell
cd "D:\Documents\stage oil kam"
python -m app.server
```

Terminal 2 :

```powershell
ngrok http 8000
```

Ngrok affiche une ligne `Forwarding` avec une adresse du type :

```text
https://exemple.ngrok-free.app
```

C'est ce lien HTTPS qu'il faut envoyer au tuteur.

### Hébergement réel

Pour un accès plus stable, il est possible d'utiliser une plateforme comme Render, Railway ou PythonAnywhere.

Différence avec un tunnel :

- l'application n'a pas besoin que votre PC reste allumé ;
- l'adresse est plus stable ;
- la configuration prend plus de temps ;
- SQLite peut nécessiter une configuration de stockage persistant selon la plateforme.

Le serveur est compatible avec ce type d'hébergement : il lit automatiquement la variable d'environnement `PORT` et écoute sur `0.0.0.0` lorsqu'un port est fourni par la plateforme.

Fichiers prévus :

- `requirements.txt` : aucune dépendance externe nécessaire ;
- `Procfile` : commande de démarrage `python -m app.server`.

## Tests

```powershell
python -m unittest discover -s tests
```

Les tests couvrent :

- création des comptes de démonstration ;
- création et modification d'utilisateurs ;
- protection du dernier administrateur actif ;
- création et modification de produits ;
- déclaration d'une perte et calcul automatique ;
- validation d'une tâche ;
- validation d'une formation et génération d'attestation ;
- règle d'accès admin selon le rôle.

## Parcours à tester

### Employé

1. Se connecter avec `employe@oilkam.demo`.
2. Consulter les tâches du jour.
3. Valider une tâche avec commentaire.
4. Aller dans `Pertes` et déclarer une perte.
5. Aller dans `Formations`, lire un module et valider un quiz.
6. Ouvrir l'attestation si le quiz est réussi.

### Manager

1. Se connecter avec `manager@oilkam.demo`.
2. Consulter les statistiques du tableau de bord.
3. Créer une tâche.
4. Consulter l'historique des tâches.
5. Consulter les pertes et exporter le CSV.
6. Ouvrir `Rapports` et tester l'impression.
7. Consulter la progression formation des employés.

### Admin

1. Se connecter avec `admin@oilkam.demo`.
2. Gérer les utilisateurs.
3. Gérer les produits.
4. Gérer les modules de formation et les quiz.
5. Gérer les tâches.
6. Consulter les pertes et rapports.

## Structure du projet

```text
app/
  __init__.py
  database.py       Schéma SQLite, migrations simples, données démo, helpers métier
  server.py         Serveur web, routes, permissions, vues HTML
  static/
    app.js          Aide aux comptes de démonstration
    styles.css      Design responsive et styles imprimables
data/
  .gitkeep
  oilkam.db         Base locale générée automatiquement
docs/
  architecture.md   Notes d'architecture
tests/
  test_smoke.py     Tests principaux
.gitignore
README.md
```

## Limites restantes

- Pas de vrai export `.xlsx` natif pour éviter d'ajouter une dépendance ; le CSV est compatible Excel.
- Les sessions sont en mémoire : elles disparaissent au redémarrage du serveur.
- Pas encore de déploiement production.
- Pas encore de journal d'audit complet.
- Les rapports sont simples mais imprimables.

## Prochaines étapes

- Ajouter une gestion plus avancée des produits par catégorie.
- Ajouter plusieurs questions par formation.
- Ajouter des exports Excel/PDF avec dépendances dédiées si l'entreprise le valide.
- Ajouter un journal d'activité.
- Préparer une configuration de production.
