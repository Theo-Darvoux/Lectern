import { redirect } from "next/navigation";

export default function AdminFlagsRedirect() {
  redirect("/staff/flags");
}
