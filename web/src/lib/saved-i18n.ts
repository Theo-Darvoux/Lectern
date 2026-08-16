"use client";

import { useCallback } from "react";
import { useLocale } from "next-intl";

const EN = {
  title: "Saved Library",
  description: "Your favourites and curated collections, in one place.",
  allSaved: "All saved",
  collections: "Collections",
  createCollection: "New collection",
  createFirstCollection:
    "Create your first collection to organize saved materials and folders.",
  renameCollection: "Rename collection",
  deleteCollection: "Delete collection",
  deleteCollectionTitle: "Delete collection?",
  deleteCollectionDescription:
    'Delete “{name}”? The materials and folders themselves will not be deleted.',
  collectionDeleted: "Collection deleted",
  collectionRenamed: "Collection renamed",
  collectionCreated: 'Collection “{name}” created',
  collectionNameDescription: "Choose a short name you will recognize easily.",
  collectionNamePlaceholder: "e.g. Exam revision",
  collectionActions: "Collection actions",
  collectionPickerTitle: "Add to collections",
  collectionPickerDescription:
    "Collections organize content without changing its favourite status.",
  addToCollection: "Add to collection",
  manageCollections: "Manage collections",
  noCollectionsYet: "No collections yet. Create one below.",
  itemCount: "{count} items",
  emptySaved: "Nothing saved yet",
  emptySavedDescription:
    "Star any material, folder, or module while browsing to keep it here for quick access.",
  emptyCollection: "This collection is empty",
  emptyCollectionDescription:
    "Add materials, folders, or modules from their details panel or by selecting items in your Saved library.",
  removeSaved: "Remove from Saved",
  removeFromCollection: "Remove from collection",
  loading: "Loading...",
  create: "Create",
  save: "Save",
  cancel: "Cancel",
  searchPlaceholder: "Search saved items...",
  filterAll: "All",
  filterMaterials: "Documents",
  filterDirectories: "Folders & Modules",
  filterQcm: "QCMs",
  filterMedia: "Media & Videos",
  filterLinks: "Links",
  sortBy: "Sort by",
  sortRecent: "Recently added",
  sortOldest: "Oldest added",
  sortNameAsc: "Name (A–Z)",
  sortNameDesc: "Name (Z–A)",
  selectAll: "Select all",
  deselectAll: "Deselect all",
  selectedCount: "{count} selected",
  batchAddToCollection: "Add to collection",
  batchRemoveFromCollection: "Remove from collection",
  batchRemoveFromSaved: "Remove from Saved",
  batchSuccessAdded: "Added {count} items to “{name}”",
  batchSuccessRemoved: "Removed {count} items",
  noSearchResults: "No items match your search",
  noSearchResultsDescription: "Try adjusting your search terms or active filters.",
  clearSearch: "Clear search",
  clearFilters: "Reset filters",
  browseMaterials: "Browse library",
  viewGrid: "Grid view",
  viewList: "List view",
  copyLink: "Copy link",
  linkCopied: "Link copied to clipboard",
  openInNewTab: "Open in new tab",
  statsSavedCount: "Saved items",
  statsCollectionsCount: "Collections",
  statsFoldersCount: "Folders",
  statsMaterialsCount: "Materials",
  addedOn: "Added {date}",
  addedRelative: "Added {relative}",
  selectItem: "Select item",
  actions: "Actions",
  batchActions: "Batch actions",
  searchCollections: "Filter collections...",
  "errors.load": "Could not load your Saved library",
  "errors.loadCollections": "Could not load collections",
  "errors.loadCollection": "Could not load this collection",
  "errors.createCollection": "Could not create collection",
  "errors.saveCollection": "Could not save collection",
  "errors.deleteCollection": "Could not delete collection",
  "errors.updateCollection": "Could not update collection membership",
  "errors.removeFromCollection": "Could not remove item from collection",
  "errors.removeSaved": "Could not remove item from Saved",
} as const;

export type SavedMessageKey = keyof typeof EN;
type SavedMessageValues = { name?: string; count?: number; date?: string; relative?: string };

const FR: Record<SavedMessageKey, string> = {
  title: "Bibliothèque d'enregistrés",
  description: "Vos favoris et collections personnalisées, réunis au même endroit.",
  allSaved: "Tous les enregistrés",
  collections: "Collections",
  createCollection: "Nouvelle collection",
  createFirstCollection:
    "Créez votre première collection pour organiser les documents et dossiers enregistrés.",
  renameCollection: "Renommer la collection",
  deleteCollection: "Supprimer la collection",
  deleteCollectionTitle: "Supprimer la collection ?",
  deleteCollectionDescription:
    "Supprimer « {name} » ? Les documents et dossiers eux-mêmes ne seront pas supprimés.",
  collectionDeleted: "Collection supprimée",
  collectionRenamed: "Collection renommée",
  collectionCreated: "Collection « {name} » créée",
  collectionNameDescription: "Choisissez un nom court que vous reconnaîtrez facilement.",
  collectionNamePlaceholder: "ex. Révisions d'examen",
  collectionActions: "Actions de la collection",
  collectionPickerTitle: "Ajouter aux collections",
  collectionPickerDescription:
    "Les collections organisent le contenu sans modifier son statut de favori.",
  addToCollection: "Ajouter à une collection",
  manageCollections: "Gérer les collections",
  noCollectionsYet: "Aucune collection. Créez-en une ci-dessous.",
  itemCount: "{count} éléments",
  emptySaved: "Aucun élément enregistré",
  emptySavedDescription:
    "Ajoutez des étoiles aux documents, dossiers ou modules lors de votre navigation pour les retrouver ici.",
  emptyCollection: "Cette collection est vide",
  emptyCollectionDescription:
    "Ajoutez des documents, dossiers ou modules depuis leur volet de détails ou en sélectionnant des éléments dans votre bibliothèque.",
  removeSaved: "Retirer des enregistrés",
  removeFromCollection: "Retirer de la collection",
  loading: "Chargement...",
  create: "Créer",
  save: "Enregistrer",
  cancel: "Annuler",
  searchPlaceholder: "Rechercher dans les enregistrés...",
  filterAll: "Tout",
  filterMaterials: "Documents",
  filterDirectories: "Dossiers & Modules",
  filterQcm: "QCMs",
  filterMedia: "Médias & Vidéos",
  filterLinks: "Liens",
  sortBy: "Trier par",
  sortRecent: "Récemment ajoutés",
  sortOldest: "Plus anciens",
  sortNameAsc: "Nom (A–Z)",
  sortNameDesc: "Nom (Z–A)",
  selectAll: "Tout sélectionner",
  deselectAll: "Tout désélectionner",
  selectedCount: "{count} sélectionné(s)",
  batchAddToCollection: "Ajouter à une collection",
  batchRemoveFromCollection: "Retirer de la collection",
  batchRemoveFromSaved: "Retirer des enregistrés",
  batchSuccessAdded: "{count} éléments ajoutés à « {name} »",
  batchSuccessRemoved: "{count} éléments retirés",
  noSearchResults: "Aucun résultat trouvé",
  noSearchResultsDescription: "Essayez de modifier votre recherche ou vos filtres.",
  clearSearch: "Effacer la recherche",
  clearFilters: "Réinitialiser les filtres",
  browseMaterials: "Explorer la bibliothèque",
  viewGrid: "Vue en grille",
  viewList: "Vue en liste",
  copyLink: "Copier le lien",
  linkCopied: "Lien copié dans le presse-papiers",
  openInNewTab: "Ouvrir dans un nouvel onglet",
  statsSavedCount: "Enregistrés",
  statsCollectionsCount: "Collections",
  statsFoldersCount: "Dossiers",
  statsMaterialsCount: "Documents",
  addedOn: "Ajouté le {date}",
  addedRelative: "Ajouté {relative}",
  selectItem: "Sélectionner l'élément",
  actions: "Actions",
  batchActions: "Actions groupées",
  searchCollections: "Filtrer les collections...",
  "errors.load": "Impossible de charger vos éléments enregistrés",
  "errors.loadCollections": "Impossible de charger les collections",
  "errors.loadCollection": "Impossible de charger cette collection",
  "errors.createCollection": "Impossible de créer la collection",
  "errors.saveCollection": "Impossible d'enregistrer la collection",
  "errors.deleteCollection": "Impossible de supprimer la collection",
  "errors.updateCollection": "Impossible de modifier l'appartenance à la collection",
  "errors.removeFromCollection": "Impossible de retirer l'élément de la collection",
  "errors.removeSaved": "Impossible de retirer l'élément des enregistrés",
};

const MESSAGES = { en: EN, fr: FR } as const;

export function useSavedTranslations() {
  const locale = useLocale();
  const language = locale.toLowerCase().startsWith("fr") ? "fr" : "en";

  return useCallback(
    (key: SavedMessageKey, values: SavedMessageValues = {}): string => {
      if (key === "itemCount") {
        const count = values.count ?? 0;
        if (language === "fr") return `${count} ${count === 1 ? "élément" : "éléments"}`;
        return `${count} ${count === 1 ? "item" : "items"}`;
      }

      if (key === "selectedCount") {
        const count = values.count ?? 0;
        if (language === "fr") return `${count} sélectionné${count > 1 ? "s" : ""}`;
        return `${count} selected`;
      }

      const template = MESSAGES[language][key] ?? MESSAGES.en[key] ?? key;
      return template.replace(/\{(name|count|date|relative)\}/g, (match, token: string) => {
        const value = values[token as keyof SavedMessageValues];
        return value === undefined ? match : String(value);
      });
    },
    [language],
  );
}
