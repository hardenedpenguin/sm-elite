# Users and accounts

The first visit asks for the admin account (see [Install](install.md) for the setup code). After that there are three roles:

- **Viewers** watch the node cards, last heard, and favorites.
- **Operators** also control nodes, optionally limited to certain node numbers, and get Announce, Schedule, Who can connect, DVSwitch, and the System page.
- **Admins** also manage users and the [admin tools](admin.md).

The control bar shows only for operators and admins, and lists only the nodes their account may control. Visitors who are not logged in can watch when `public = read` in `[server]`; with `public = none` they see nothing until they log in.

Anyone logged in can change their own password with **Account** in the header. It asks for the current password, and logs out every other browser signed in as that user.

Admins open **Users** to add, edit, or remove accounts. Edit changes the role and the node limit, and can set a new password (leave it blank to keep the current one); a new password logs that user out everywhere. The last admin cannot be removed or turned into an operator or viewer.
