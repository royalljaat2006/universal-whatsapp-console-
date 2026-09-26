import Link from "next/link";

import { AuthShell } from "@/features/auth/auth-shell";
import { LoginForm } from "@/features/auth/login-form";

export default function LoginPage() {
  return (
    <AuthShell
      title="Welcome back"
      description="Sign in to your workspace"
      footer={
        <>
          Don&rsquo;t have a workspace yet?{" "}
          <Link href="/register" className="text-primary hover:underline">
            Create one
          </Link>
          .
        </>
      }
    >
      <LoginForm />
    </AuthShell>
  );
}
