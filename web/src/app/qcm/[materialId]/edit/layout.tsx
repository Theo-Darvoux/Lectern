import { QCMEditPageContent } from "@/components/qcm/qcm-edit-page-content";

// Renders the editor itself. It remounts when the QCM id changes, so each QCM
// starts from a fresh editor state.
export default function EditQCMLayout() {
  return <QCMEditPageContent />;
}
