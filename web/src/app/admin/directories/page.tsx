import { redirect } from "next/navigation";

export default function AdminDirectoriesRedirect() {
  redirect("/staff/content?tab=directories");
}
