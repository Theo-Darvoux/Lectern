import { redirect } from "next/navigation";

export default function ModeratorDirectoriesRedirect() {
  redirect("/staff/content?tab=directories");
}
