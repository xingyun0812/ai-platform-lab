"""预定义 seccomp 配置集合 — Phase I #41

SECCOMP_PROFILES 是一组符合 Docker seccomp JSON 格式的配置字典，
可直接用于 `docker run --security-opt seccomp=<json_file>`。
"""

from __future__ import annotations

# Docker seccomp JSON 格式
# defaultAction: 默认操作（SCMP_ACT_ERRNO = 拒绝并返回 EPERM）
# syscalls: 覆盖特定系统调用的动作列表

SECCOMP_PROFILES: dict[str, dict] = {
    "strict": {
        # 仅允许最基本的系统调用：读写、退出、内存映射等
        "defaultAction": "SCMP_ACT_ERRNO",
        "architectures": ["SCMP_ARCH_X86_64", "SCMP_ARCH_X86", "SCMP_ARCH_X32"],
        "syscalls": [
            {
                "names": [
                    "read",
                    "write",
                    "exit",
                    "exit_group",
                    "brk",
                    "mmap",
                    "mprotect",
                    "munmap",
                    "rt_sigreturn",
                    "sigreturn",
                    "futex",
                    "nanosleep",
                    "clock_gettime",
                    "gettimeofday",
                    "close",
                    "fstat",
                    "lseek",
                ],
                "action": "SCMP_ACT_ALLOW",
            }
        ],
    },
    "default": {
        # 允许常见系统调用，拒绝挂载/重启/chroot 等危险调用
        "defaultAction": "SCMP_ACT_ALLOW",
        "architectures": ["SCMP_ARCH_X86_64", "SCMP_ARCH_X86", "SCMP_ARCH_X32"],
        "syscalls": [
            {
                "names": [
                    "mount",
                    "umount",
                    "umount2",
                    "reboot",
                    "chroot",
                    "pivot_root",
                    "swapon",
                    "swapoff",
                    "kexec_load",
                    "kexec_file_load",
                    "init_module",
                    "finit_module",
                    "delete_module",
                    "ptrace",
                    "acct",
                    "settimeofday",
                    "adjtimex",
                    "clock_settime",
                    "stime",
                    "nfsservctl",
                    "bdflush",
                    "add_key",
                    "request_key",
                    "keyctl",
                ],
                "action": "SCMP_ACT_ERRNO",
            }
        ],
    },
    "networking": {
        # 允许 socket/connect，拒绝 bind（防止监听端口）
        "defaultAction": "SCMP_ACT_ALLOW",
        "architectures": ["SCMP_ARCH_X86_64", "SCMP_ARCH_X86", "SCMP_ARCH_X32"],
        "syscalls": [
            {
                "names": [
                    "bind",
                    "listen",
                    "mount",
                    "umount",
                    "umount2",
                    "reboot",
                    "chroot",
                    "ptrace",
                ],
                "action": "SCMP_ACT_ERRNO",
            },
            {
                "names": [
                    "socket",
                    "connect",
                    "sendto",
                    "recvfrom",
                    "sendmsg",
                    "recvmsg",
                    "getsockopt",
                    "setsockopt",
                    "getsockname",
                    "getpeername",
                ],
                "action": "SCMP_ACT_ALLOW",
            },
        ],
    },
    "readonly": {
        # 禁止写入系统调用
        "defaultAction": "SCMP_ACT_ALLOW",
        "architectures": ["SCMP_ARCH_X86_64", "SCMP_ARCH_X86", "SCMP_ARCH_X32"],
        "syscalls": [
            {
                "names": [
                    "write",
                    "pwrite64",
                    "writev",
                    "pwritev",
                    "pwritev2",
                    "open",
                    "openat",
                    "openat2",
                    "creat",
                    "truncate",
                    "ftruncate",
                    "unlink",
                    "unlinkat",
                    "rename",
                    "renameat",
                    "renameat2",
                    "mkdir",
                    "mkdirat",
                    "rmdir",
                    "link",
                    "linkat",
                    "symlink",
                    "symlinkat",
                    "chmod",
                    "fchmod",
                    "fchmodat",
                    "chown",
                    "fchown",
                    "lchown",
                    "fchownat",
                    "setxattr",
                    "lsetxattr",
                    "fsetxattr",
                    "removexattr",
                    "lremovexattr",
                    "fremovexattr",
                    "mount",
                    "umount",
                    "umount2",
                    "reboot",
                    "chroot",
                    "ptrace",
                ],
                "action": "SCMP_ACT_ERRNO",
            }
        ],
    },
}
