# Oil Kam - Application de gestion station-service

Application web de démonstration pour une station-service Oil Kam. Elle couvre les besoins principaux du cahier des charges : tâches quotidiennes, pertes de marchandises, formations internes, rapports et gestion des rôles.

La version actuelle est une base fonctionnelle et présentable : elle privilégie la stabilité, la simplicité et la lisibilité du code. Elle utilise Python standard et SQLite, sans framework ni dépendance externe.

## Objectif

L'objectif de l'application est de fournir une base de gestion simple pour une station-service Oil Kam : suivi des tâches quotidiennes, pointage, déclaration des pertes, formations internes, rapports et gestion des accès selon les rôles.

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

### Module pointage

- Page dédiée `/pointage`.
- Pointage d'arrivée et de départ pour les employés.
- Historique du jour.
- Consultation des pointages par manager/admin.
- Enregistrement en SQLite : utilisateur, date, heure et type de pointage.

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

- Guide opérationnel consultable par employé, manager et admin.
- 5 parties métier : Extérieur, Caisse, Périmé, Température, FDG.
- Cartes simples avec description et accès aux tâches.
- Pages détaillées avec check-lists de tâches à connaître ou vérifier.
- Exemples réalistes intégrés en attendant les consignes définitives du tuteur.
- Quiz, score et attestation retirés du parcours principal pour cette version.

### Rapports

- Rapport imprimable des pertes par période.
- Rapport imprimable des tâches complétées.
- Rapport imprimable de progression des formations, conservé comme base technique à adapter.
- Bouton d'impression navigateur.
- Export CSV conservé pour les pertes.

### Interface et PWA

- Page de connexion plein écran, sans affichage public des comptes de démonstration.
- Charte visuelle sobre : jaune, gris, noir et blanc.
- Navigation basse adaptée mobile/tablette.
- Manifest PWA et service worker simple.
- Icônes applicatives préparées dans `app/static/icons/`.

## Lancer l'application

Prérequis : Python 3.11 ou plus récent.

```powershell
cd oilkam-station-management
python -m app.server
```

Adresse locale :

```text
http://127.0.0.1:8000
```

La base SQLite est créée automatiquement dans `data/oilkam.db`.

## Mise en ligne / Démonstration à distance

### Accès depuis un téléphone sur le même Wi-Fi

Cette solution fonctionne si le PC et le téléphone sont connectés au même réseau Wi-Fi.

Lancer le serveur en écoutant sur le réseau local :

```powershell
python -m app.server --host 0.0.0.0 --port 8000
```

Trouver l'adresse IP locale du PC :

```powershell
ipconfig
```

Chercher la ligne `Adresse IPv4`, par exemple `192.168.1.25`.

Depuis le téléphone, ouvrir :

```text
http://ADRESSE_IP_DU_PC:8000
```

Exemple :

```text
http://192.168.1.25:8000
```

Si la page ne s'ouvre pas, Windows peut bloquer le port. Il faut autoriser Python dans le pare-feu Windows pour les réseaux privés.

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
cd oilkam-station-management
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
cd oilkam-station-management
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
- disponibilité des catégories de formation opérationnelle ;
- règle d'accès admin selon le rôle.

## Parcours à tester

### Employé

1. Se connecter avec `employe@oilkam.demo`.
2. Consulter les tâches du jour.
3. Valider une tâche avec commentaire.
4. Aller dans `Pointage` et pointer l'arrivée ou le départ.
5. Aller dans `Pertes` et déclarer une perte.
6. Aller dans `Formations`.
7. Ouvrir les parties `Extérieur`, `Caisse`, `Périmé`, `Température` et `FDG`.
8. Consulter les check-lists métier.

### Manager

1. Se connecter avec `manager@oilkam.demo`.
2. Consulter les statistiques du tableau de bord.
3. Créer une tâche.
4. Consulter l'historique des tâches.
5. Consulter les pointages du jour.
6. Consulter les pertes et exporter le CSV.
7. Ouvrir `Rapports` et tester l'impression.
8. Consulter les guides opérationnels dans `Formation`.

### Admin

1. Se connecter avec `admin@oilkam.demo`.
2. Gérer les utilisateurs.
3. Gérer les produits.
4. Consulter le guide opérationnel de formation.
5. Gérer les tâches.
6. Consulter les pertes et rapports.

## Structure du projet

```text
app/
  __init__.py
  database.py       Schéma SQLite, migrations simples, données démo, helpers métier
  server.py         Serveur web, routes, permissions, vues HTML
  static/
    app.js          Navigation active et enregistrement du service worker
    manifest.json   Métadonnées PWA
    service-worker.js Cache PWA simple
    styles.css      Design responsive et styles imprimables
    icons/          Icônes PWA simples
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
- Le mode PWA reste simple et dépend du navigateur.
- Pas encore de déploiement production avancé.
- Pas encore de journal d'audit complet.
- Les rapports sont simples mais imprimables.

## Prochaines étapes

- Ajouter une gestion plus avancée des produits par catégorie.
- Ajouter plusieurs questions par formation.
- Ajouter une vue pointage par période.
- Ajouter des exports Excel/PDF avec dépendances dédiées si l'entreprise le valide.
- Ajouter un journal d'activité.
- Préparer une configuration de production.
