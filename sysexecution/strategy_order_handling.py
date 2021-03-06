"""
Called from sysproduction code in a while loop, each time it runs loops over strategies
For each strategy gets the required trades per instrument
It then passes these to the 'virtual' order queue
So called because it deals with instrument level trades, not contract implementation
"""

from syscore.objects import missing_order,  success, failure, locked_order, duplicate_order

from syscore.genutils import timerClass
from syscore.objects import resolve_function, not_updated, success, failure

from sysdata.private_config import get_private_then_default_key_value

from sysproduction.data.positions import diagPositions

class orderHandlerAcrossStrategies(object):
    def __init__(self, data):
        data.add_class_list("mongoInstrumentOrderStackData")
        self.data = data
        self._create_strategy_generators()

    @property
    def order_stack(self):
        return self.data.mongo_instrument_order_stack

    def _create_strategy_generators(self):
        strategy_dict = get_private_then_default_key_value('strategy_list')
        generator_dict = {}
        for strategy_name, strategy_config in strategy_dict.items():
            self.data.log.label(strategy_name = strategy_name)
            config_dict = get_private_then_default_key_value('strategy_list')
            try:
                config_for_strategy = config_dict[strategy_name]
                order_config = config_for_strategy['order_handling']
                strategy_handler_class_name = order_config['function']
                strategy_handler_class = resolve_function(strategy_handler_class_name)

            except:
                self.data.log.critical("No handler found for strategy %s, won't do order handling" % strategy_name)
                strategy_handler_class = orderGeneratorForStrategy

            strategy_handler = strategy_handler_class(self.data, strategy_name)
            generator_dict[strategy_name] = strategy_handler

        self._generator_dict = generator_dict

        return success

    def check_for_orders_across_strategies(self):
        """
        This function is called every time we want to see if any of our strategies have an order
        If new orders are generated, it will handle them

        :return: succcess
        """
        generator_dict = self._generator_dict
        for strategy_name, order_generator in generator_dict.items():
            order_list = order_generator.required_orders_if_updated()
            if order_list is not_updated:
                # next strategy
                continue
            else:
                ## Handle the orders
                result = self.submit_order_list(order_list)
                if result is success:
                    generator_dict[strategy_name].set_last_run()

        return success

    def submit_order_list(self, order_list):
        for order in order_list:
            log = self.data.log.setup(strategy_name=order.strategy_name, instrument_code = order.instrument_code)
            try:
                result = self.order_stack.put_order_on_stack(order)
                if result is success:
                    log.msg("Added order %s to instrument order stack" % str(order))
                elif result is duplicate_order:
                    log.msg("Order %s already on instrument order stack" % str(order))
                elif result is locked_order:
                    log.msg("Order %s is locked in instrument order stack can't modify" % str(order))
                else:
                    log.msg("Order %s won't update with status %s" % (str(order), str(result)))

            except Exception as e:
                # serious error, abandon everything
                log.critical("Problem %s adding order %s to stack %s" % (e, str(order), str(self.order_stack)))
                return failure

        return success

class orderGeneratorForStrategy(timerClass):
    """

    Order generators are strategy specific but have common methods used by the order handler

    """

    def __init__(self, data, strategy_name):
        self.data = data
        self.strategy_name = strategy_name

    @property
    def strategy_config(self):
        config = getattr(self, "_strategy_config", None)
        if config is None:
            try:
                config_dict = get_private_then_default_key_value('strategy_list')
                config_for_strategy = config_dict[self.strategy_name]
                config = config_for_strategy['order_handling']
            except Exception as e:
                self.data.log.critical("Can't find order_handling configuration for strategy in .strategy_list config element error %s" % e)
                return {}

            self._strategy_config = config

        return config

    @property
    def frequency_minutes(self):
        # used by timer code
        # defaults to every hour unless otherwise
        return self.strategy_config.get('frequency_minutes', 60)

    def get_actual_positions_for_strategy(self):
        """
        Actual positions held by a strategy

        :return: dict, keys are instrument codes, values are positions
        """
        data = self.data
        strategy_name = self.strategy_name

        diag_positions = diagPositions(data)
        list_of_instruments = diag_positions.get_list_of_instruments_for_strategy_with_position(strategy_name)
        actual_positions = dict([(instrument_code,
                                  diag_positions.get_position_for_strategy_and_instrument(strategy_name,
                                                                                          instrument_code))
                                 for instrument_code in list_of_instruments])
        return actual_positions

    def required_orders_if_updated(self):
        """
        Called by handler for all strategies

        :return: dict, keys are instrument codes, values are trades OR not_updated
        """

        requires_update = self.check_if_ready_for_another_run()
        if not requires_update:
            return not_updated

        ## needs updating
        orders = self._required_orders_no_checking()

        return orders

    def _required_orders_no_checking(self):
        ## Would normally be overriden, we only use this class if no class is found in config
        return not_updated



