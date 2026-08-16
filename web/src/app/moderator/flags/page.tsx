import { redirect } from "next/navigation";

export default function ModeratorFlagsRedirect() {
  redirect("/staff/flags");
}
