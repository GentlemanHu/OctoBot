#  This file is part of OctoBot (https://github.com/Drakkar-Software/OctoBot)
#  Copyright (c) 2023 Drakkar-Software, All rights reserved.
#
#  OctoBot is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either
#  version 3.0 of the License, or (at your option) any later version.
#
#  OctoBot is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  General Public License for more details.
#
#  You should have received a copy of the GNU General Public
#  License along with OctoBot. If not, see <https://www.gnu.org/licenses/>.

import os
import typing

import sys
import asyncio
import signal
import threading
import subprocess

import octobot_commons.configuration as configuration
import octobot_commons.profiles as profiles
import octobot_commons.logging as logging
import octobot_commons.constants as commons_constants
import octobot_commons.errors as commons_errors
import octobot_commons.aiohttp_util as aiohttp_util

import octobot_tentacles_manager.api as tentacles_manager_api
import octobot_tentacles_manager.cli as tentacles_manager_cli

import octobot.api.strategy_optimizer as strategy_optimizer_api
import octobot.logger as octobot_logger
import octobot.constants as constants
import octobot.community.tentacles_packages as community_tentacles_packages
import octobot.configuration_manager as configuration_manager

COMMANDS_LOGGER_NAME = "Commands"
IGNORED_COMMAND_WHEN_RESTART = ["-u", "--update"]

GLOBAL_BOT_INSTANCE = None


def call_tentacles_manager(command_args):
    octobot_logger.init_logger()
    tentacles_urls = [
        configuration_manager.get_default_tentacles_url(),
    ]
    sys.exit(tentacles_manager_cli.handle_tentacles_manager_command(command_args,
                                                                    tentacles_urls=tentacles_urls,
                                                                    bot_install_dir=os.getcwd()))
    

def exchange_keys_encrypter(catch=False):
    try:
        api_key_crypted = configuration.encrypt(input("ENTER YOUR API-KEY : ")).decode()
        api_secret_crypted = configuration.encrypt(input("ENTER YOUR API-SECRET : ")).decode()
        print(f"Here are your encrypted exchanges keys : \n "
              f"\t- API-KEY : {api_key_crypted}\n"
              f"\t- API-SECRET : {api_secret_crypted}\n\n"
              f"Your new exchange key configuration is : \n"
              f'\t"api-key": "{api_key_crypted}",\n'
              f'\t"api-secret": "{api_secret_crypted}"\n')
    except Exception as e:
        if not catch:
            logging.get_logger(COMMANDS_LOGGER_NAME).error(
                f"Fail to encrypt your exchange keys, please try again ({e}).")
            raise e


def start_strategy_optimizer(config, commands):
    tentacles_setup_config = tentacles_manager_api.get_tentacles_setup_config(config.get_tentacles_config_path())
    optimizer = strategy_optimizer_api.create_strategy_optimizer(config.config, tentacles_setup_config, commands[0])
    if strategy_optimizer_api.get_optimizer_is_properly_initialized(optimizer):
        strategy_optimizer_api.find_optimal_configuration(optimizer)
        strategy_optimizer_api.print_optimizer_report(optimizer)


def run_tentacles_install_or_update(community_auth, config):
    _check_tentacles_install_exit()
    asyncio.run(_install_or_update_tentacles(community_auth, config))


async def _install_or_update_tentacles(community_auth, config):
    additional_tentacles_package_urls = community_auth.get_saved_package_urls()
    await install_or_update_tentacles(config, additional_tentacles_package_urls, False)


def run_update_or_repair_tentacles_if_necessary(community_auth, config, tentacles_setup_config):
    asyncio.run(update_or_repair_tentacles_if_necessary(community_auth, tentacles_setup_config, config))


def _check_tentacles_install_exit():
    if constants.EXIT_BEFORE_TENTACLES_AUTO_REINSTALL:
        logging.get_logger(COMMANDS_LOGGER_NAME).info(
            "Exiting OctoBot before re-installing tentacles as EXIT_BEFORE_TENTACLES_AUTO_REINSTALL is True")
        sys.exit(0)


def _get_first_non_imported_profile_tentacles_setup_config(config):
    try:
        return tentacles_manager_api.get_tentacles_setup_config(
            config.get_non_imported_profiles()[0].get_tentacles_config_path()
        )
    except IndexError:
        return None


async def update_or_repair_tentacles_if_necessary(community_auth, selected_profile_tentacles_setup_config, config):
    local_profile_tentacles_setup_config = selected_profile_tentacles_setup_config
    logger = logging.get_logger(COMMANDS_LOGGER_NAME)
    if config.profile.imported:
        if not tentacles_manager_api.are_tentacles_up_to_date(selected_profile_tentacles_setup_config,
                                                              constants.VERSION):
            selected_profile_tentacles_version = tentacles_manager_api.get_tentacles_installation_version(
                selected_profile_tentacles_setup_config
            )
            logger.info(f"Current imported profile \"{config.profile.name}\" references tentacles in a different "
                        f"version from the current OctoBot. Referenced version: "
                        f"{selected_profile_tentacles_version}, current OctoBot version: {constants.VERSION}. "
                        f"Please make sure that this profile works on your OctoBot.")
            # only update tentacles based on local (non imported) profiles tentacles installation version
            local_profile_tentacles_setup_config = _get_first_non_imported_profile_tentacles_setup_config(config)

    to_install_urls, to_remove_tentacles, force_refresh_tentacles_setup_config = \
        community_tentacles_packages.get_to_install_and_remove_tentacles(
            community_auth, selected_profile_tentacles_setup_config, constants.LONG_VERSION
        ) if community_auth else ([], [], False)
    if to_remove_tentacles:
        await community_tentacles_packages.uninstall_tentacles(to_remove_tentacles)
    elif force_refresh_tentacles_setup_config:
        community_tentacles_packages.refresh_tentacles_setup_config()

    if local_profile_tentacles_setup_config is None or \
            not tentacles_manager_api.are_tentacles_up_to_date(local_profile_tentacles_setup_config, constants.VERSION):
        logger.info("OctoBot tentacles are not up to date. Updating tentacles...")
        _check_tentacles_install_exit()
        if await install_or_update_tentacles(config, to_install_urls, False):
            logger.info("OctoBot tentacles are now up to date.")
    else:
        if to_install_urls:
            logger.debug("Installing new tentacles.")
            # install additional tentacles only when tentacles arch is valid. Install all tentacles otherwise
            only_additional = tentacles_manager_api.is_tentacles_architecture_valid()
            await install_or_update_tentacles(config, to_install_urls, only_additional)
        if tentacles_manager_api.load_tentacles(verbose=True):
            logger.debug("OctoBot tentacles are up to date.")
        else:
            logger.info("OctoBot tentacles are damaged. Installing default tentacles only ...")
            _check_tentacles_install_exit()
            await install_or_update_tentacles(config, [], False)


async def install_or_update_tentacles(
    config, additional_tentacles_package_urls: typing.Optional[list], only_additional: bool
):
    await install_all_tentacles(
        additional_tentacles_package_urls=additional_tentacles_package_urls, only_additional=only_additional
    )
    # reload profiles
    config.load_profiles()
    # reload tentacles
    return tentacles_manager_api.load_tentacles(verbose=True)


async def install_all_tentacles(
    tentacles_url=None, additional_tentacles_package_urls: typing.Optional[list] = None, only_additional: bool = False,
    bot_version: str = constants.LONG_VERSION
):
    if tentacles_url is None:
        tentacles_url = configuration_manager.get_default_tentacles_url()
    async with aiohttp_util.ssl_fallback_aiohttp_client_session(
        commons_constants.KNOWN_POTENTIALLY_SSL_FAILED_REQUIRED_URL
    ) as aiohttp_session:
        base_urls = [tentacles_url] + community_tentacles_packages.get_env_variable_tentacles_urls()
        # avoid duplicates
        additional_urls = [
            url
            for url in (additional_tentacles_package_urls or [])
            if url not in base_urls
        ]
        for url in (base_urls if not only_additional else []) + additional_urls:
            if url is None:
                continue
            hide_url = url in additional_tentacles_package_urls
            url = community_tentacles_packages.adapt_url_to_bot_version(url, bot_version)
            await tentacles_manager_api.install_all_tentacles(
                url,
                aiohttp_session=aiohttp_session,
                bot_install_dir=os.getcwd(),
                hide_url=hide_url,
            )


def ensure_profile(config):
    if config.profile is None:
        # no selected profile or profile not found
        try:
            config.select_profile(commons_constants.DEFAULT_PROFILE)
        except KeyError:
            raise commons_errors.NoProfileError


def download_and_select_profile(logger, config, to_download_profile_urls, to_select_profile):
    if to_download_profile_urls:
        download_missing_profiles(
            config,
            to_download_profile_urls
        )
    if select_forced_profile_if_any(config, to_select_profile, logger):
        ensure_profile(config)


def download_missing_profiles(config, profile_urls):
    downloaded_profiles = []
    # dl profiles from env
    if profile_urls:
        installed_profiles_urls = set(
            profile.origin_url
            for profile in config.profile_by_id.values()
        )
        for download_url in set(profile_urls):
            if download_url not in installed_profiles_urls:
                installed_profile = profiles.download_and_install_profile(download_url, constants.PROFILE_FILE_SCHEMA)
                if installed_profile is not None:
                    downloaded_profiles.append(
                        installed_profile
                    )
    if downloaded_profiles:
        # reload profiles to load downloaded ones
        config.load_profiles()
    return downloaded_profiles


def select_forced_profile_if_any(config, forced_profile, logger) -> bool:
    if forced_profile:
        for profile in config.profile_by_id.values():
            if profile.profile_id == forced_profile \
               or profile.origin_url == forced_profile \
               or profile.name == forced_profile:
                logger.info(f"Selecting forced profile {profile.name} (identified by {forced_profile})")
                config.select_profile(profile.profile_id)
                return True
        logger.warning(f"Forced profile not found in available profiles ({forced_profile})")
    return False


def set_global_bot_instance(bot_instance):
    global GLOBAL_BOT_INSTANCE
    GLOBAL_BOT_INSTANCE = bot_instance


def _signal_handler(_, __):
    # run Commands.BOT.stop_threads in thread because can't use the current asyncio loop
    stopping_thread = threading.Thread(target=GLOBAL_BOT_INSTANCE.task_manager.stop_tasks(),
                                       name="Commands signal_handler stop_tasks")
    stopping_thread.start()
    stopping_thread.join()
    os._exit(0)


def run_bot(bot, logger):
    # handle CTRL+C signal
    try:
        signal.signal(signal.SIGINT, _signal_handler)
    except ValueError as e:
        logger.warning(f"Can't setup signal handler : {e}")

    # start bot
    bot.task_manager.run_forever(start_bot(bot, logger))


async def start_bot(bot, logger, catch=False):
    try:
        # load tentacles details
        tentacles_manager_api.reload_tentacle_info()
        # ensure tentacles config exists or create a new one
        await tentacles_manager_api.ensure_setup_configuration(bot_install_dir=os.getcwd())

        try:
            await bot.initialize()
        except asyncio.CancelledError:
            logger.info("Core engine tasks cancelled.")

    except Exception as e:
        logger.exception(e)
        if not catch:
            raise
        stop_bot(bot)


def stop_bot(bot, force=False):
    bot.task_manager.stop_tasks()
    if force:
        os._exit(0)


def get_bot_file():
    return sys.argv[0]


def restart_bot():
    argv = (f'{a}' for a in sys.argv if a not in IGNORED_COMMAND_WHEN_RESTART)
    if get_bot_file().endswith(".py"):
        os.execl(sys.executable, f'{sys.executable}', *argv)
    elif get_bot_file().endswith(constants.PROJECT_NAME):
        # restart from python OctoBot package entrypoint
        os.execl(get_bot_file(), *argv)
    else:
        # restart from binary
        # from https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#using-sys-executable-to-spawn-subprocesses-that-outlive-the-application-process-implementing-application-restart
        # Restart the application
        subprocess.Popen([sys.executable], env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"})
        # force stop and restart
        os._exit(0)


def update_bot(bot_api):
    import octobot.updater.updater_factory as updater_factory
    bot_api.run_in_async_executor(updater_factory.create_updater().update())
