import { redirect } from "next/navigation";

export default function AdminBackupRedirect() {
  redirect("/staff/backup");
}
