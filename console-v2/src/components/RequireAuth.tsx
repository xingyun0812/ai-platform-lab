import React from "react";
import { Navigate, useLocation } from "react-router-dom";

interface RequireAuthProps {
  children: React.ReactNode;
}

/**
 * HOC that checks localStorage for a token.
 * If missing, redirects to /login, preserving the intended URL.
 */
export default function RequireAuth({ children }: RequireAuthProps) {
  const location = useLocation();
  const token = localStorage.getItem("token");

  if (!token) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}
