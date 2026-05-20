export type WorkspaceCommandTarget = {
  remotePath?: string;
  port?: string;
  kind?: "file" | "folder" | "placeholder";
};
