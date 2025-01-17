/*
 * EntryPoint.cpp, part of VCMI engine
 *
 * Authors: listed in file AUTHORS in main folder
 *
 * License: GNU General Public License v2.0 or later
 * Full text of license available in license.txt file, in main folder
 *
 */
#include "StdInc.h"

#include "LobbyServer.h"

#include "../lib/logging/CBasicLogConfigurator.h"
#include "../lib/filesystem/CFilesystemLoader.h"
#include "../lib/filesystem/Filesystem.h"
#include "../lib/VCMIDirs.h"

static const int LISTENING_PORT = 30303;

int main(int argc, const char * argv[])
{
	CResourceHandler::initialize();
	CResourceHandler::load("config/filesystem.json"); // FIXME: we actually need only config directory for schemas, can be reduced

#ifndef VCMI_IOS
	console = new CConsoleHandler();
#endif
	CBasicLogConfigurator logConfig(VCMIDirs::get().userLogsPath() / "VCMI_Lobby_log.txt", console);
	logConfig.configureDefault();

	auto databasePath = VCMIDirs::get().userDataPath() / "vcmiLobby.db";
	logGlobal->info("Opening database %s", databasePath.string());

	LobbyServer server(databasePath);
	logGlobal->info("Starting server on port %d", LISTENING_PORT);

	server.start(LISTENING_PORT);
	server.run();

	return 0;
}
