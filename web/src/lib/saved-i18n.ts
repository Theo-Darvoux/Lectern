"use client";

import { useCallback } from "react";
import { useLocale } from "next-intl";

const EN = {
  title: "Saved",
  description: "Your favourites and collections, in one place.",
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
    "Use the star on a material, folder, or module to keep it here.",
  emptyCollection: "This collection is empty",
  emptyCollectionDescription:
    "Add materials, folders, or modules from their details panel or from your Saved library.",
  removeSaved: "Remove from Saved",
  removeFromCollection: "Remove from collection",
  loading: "Loading...",
  create: "Create",
  save: "Save",
  cancel: "Cancel",
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

type SavedMessageKey = keyof typeof EN;
type SavedMessageValues = { name?: string; count?: number };

const FR: Record<SavedMessageKey, string> = {
  title: "Enregistrés",
  description: "Vos favoris et collections, réunis au même endroit.",
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
    "Utilisez l'étoile sur un document, dossier ou module pour le conserver ici.",
  emptyCollection: "Cette collection est vide",
  emptyCollectionDescription:
    "Ajoutez des documents, dossiers ou modules depuis leur panneau de détails ou depuis vos éléments enregistrés.",
  removeSaved: "Retirer des enregistrés",
  removeFromCollection: "Retirer de la collection",
  loading: "Chargement...",
  create: "Créer",
  save: "Enregistrer",
  cancel: "Annuler",
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

      return MESSAGES[language][key].replace(/\{(name|count)\}/g, (match, token: string) => {
        const value = values[token as keyof SavedMessageValues];
        return value === undefined ? match : String(value);
      });
    },
    [language],
  );
}
